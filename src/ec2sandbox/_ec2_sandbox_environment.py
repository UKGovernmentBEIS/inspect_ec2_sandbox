import errno
import os
import random
import shlex
import string
import sys
from datetime import datetime
from logging import getLogger
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Union

import boto3
from botocore.exceptions import ClientError, WaiterError
from inspect_ai.util import (
    ExecResult,
    OutputLimitExceededError,
    SandboxConnection,
    SandboxEnvironment,
    SandboxEnvironmentConfigType,
    SandboxEnvironmentLimits,
    sandboxenv,
)
from pydantic import BaseModel
from rich import box, print
from rich.prompt import Confirm
from rich.table import Table
from tenacity import retry, stop_after_attempt, wait_fixed
from typing_extensions import Literal, override

from ec2sandbox._instance_provider import (
    Ec2InstanceProvider,
    SandboxInstanceInfo,
    get_ec2_instance_provider,
    get_provider_session,
)
from ec2sandbox.schema import Ec2SandboxEnvironmentConfig

from ._unpack_tags import convert_tags_for_aws_interface


@retry(stop=stop_after_attempt(20), wait=wait_fixed(30))
def _wait_for_ssm(instance_id: str, ssm_client: Any) -> bool:
    """Wait for SSM agent to come online on the given instance."""
    resp = ssm_client.describe_instance_information(
        InstanceInformationFilterList=[
            {"key": "InstanceIds", "valueSet": [instance_id]}
        ]
    )
    if (
        not resp["InstanceInformationList"]
        or resp["InstanceInformationList"][0]["PingStatus"] != "Online"
    ):
        raise Exception("Not ready")
    return True


MARKER_TAG_KEY = "inspect_sandbox"


@sandboxenv(name="ec2")
class Ec2SandboxEnvironment(SandboxEnvironment):
    """An Inspect sandbox environment for EC2 virtual machines."""

    logger = getLogger(__name__)

    TRACE_NAME = "ec2_sandbox_environment"

    _session: ClassVar[boto3.Session | None] = None

    @classmethod
    def set_session(cls, session: boto3.Session) -> None:
        """Set the boto3 session used for all AWS operations.

        Call this before running any evals to supply explicit credentials.
        If not called, a default ``boto3.Session()`` is used.
        """
        cls._session = session

    @classmethod
    def _get_session(cls) -> boto3.Session:
        if cls._session is None:
            cls._session = boto3.Session()
        return cls._session

    def __init__(
        self,
        instance_id: str,
        region: str,
        s3_bucket: str,
        s3_key_prefix: str = "",
    ):
        self.instance_id = instance_id
        self.region = region
        self.s3_bucket = s3_bucket
        self.s3_key_prefix = s3_key_prefix
        session = self._get_session()
        self.ssm_client = session.client("ssm", region_name=region)
        self.s3_client = session.client(
            "s3",
            region_name=region,
            endpoint_url=f"https://s3.{region}.amazonaws.com",
        )
        self.ec2_client = session.client("ec2", region_name=region)

    @classmethod
    @override
    async def task_init(
        cls, task_name: str, config: SandboxEnvironmentConfigType | None
    ) -> None:
        # If a custom provider supplies a session, adopt it before any
        # samples run so all runtime boto3 calls share the same credentials.
        provider = get_ec2_instance_provider()
        provider_session = get_provider_session(provider)
        if provider_session is not None:
            cls.set_session(provider_session)
        else:
            # Ensure the default session is initialised before any samples run.
            cls._get_session()

    @classmethod
    @override
    async def sample_init(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        metadata: dict[str, str],
    ) -> dict[str, SandboxEnvironment]:
        provider = get_ec2_instance_provider()

        # Base tags shared by both the custom-provider and default paths.
        # Each path prepends config.extra_tags in its own way.
        tags = [
            ("Name", f"inspect_ec2_sandbox_{task_name}"),
            ("inspect_task", task_name),
            (MARKER_TAG_KEY, "true"),
        ]

        if provider is not None:
            # Custom provider path — provider handles all provisioning.
            instance_type = (
                config.instance_type
                if isinstance(config, Ec2SandboxEnvironmentConfig)
                else "t3a.large"
            )
            ami_id = (
                config.ami_id
                if isinstance(config, Ec2SandboxEnvironmentConfig)
                else ""
            )
            if isinstance(config, Ec2SandboxEnvironmentConfig) and config.extra_tags:
                tags = list(config.extra_tags) + tags

            cls.logger.debug(
                "sample_init: custom provider, type=%s ami=%s",
                instance_type,
                ami_id,
            )
            result = await provider.create_instance(
                instance_type=instance_type,
                ami_id=ami_id,
                tags=tags,
            )
            cls.logger.debug(
                "sample_init: provider returned id=%s region=%s",
                result.instance_id,
                result.region,
            )
            environment = Ec2SandboxEnvironment(
                instance_id=result.instance_id,
                region=result.region,
                s3_bucket=result.s3_bucket,
                s3_key_prefix=result.s3_key_prefix,
            )
            return {"default": environment}

        # Default path — direct EC2/SSM calls using Ec2SandboxEnvironmentConfig.
        session = cls._get_session()
        if config is None:
            config = Ec2SandboxEnvironmentConfig.from_settings(session=session)
        if not isinstance(config, Ec2SandboxEnvironmentConfig):
            raise ValueError("config must be a Ec2SandboxEnvironmentConfig")

        ec2_client = session.client("ec2", region_name=config.region)

        tags = list(config.extra_tags) + tags

        instance_params = {
            "ImageId": config.ami_id,
            "InstanceType": config.instance_type,
            "SecurityGroupIds": [config.security_group_id],
            "SubnetId": config.subnet_id,
            "TagSpecifications": convert_tags_for_aws_interface(
                "instance", tuple(tags)
            ),
            "IamInstanceProfile": {"Name": config.instance_profile},
        }

        response = ec2_client.run_instances(**instance_params, MinCount=1, MaxCount=1)

        instance = response["Instances"][0]
        waiter = ec2_client.get_waiter("instance_running")
        waiter.wait(InstanceIds=[instance["InstanceId"]])

        environment = Ec2SandboxEnvironment(
            instance_id=instance["InstanceId"],
            region=config.region,
            s3_bucket=config.s3_bucket,
            s3_key_prefix=config.s3_key_prefix,
        )

        _wait_for_ssm(environment.instance_id, environment.ssm_client)

        return {"default": environment}

    @classmethod
    @override
    async def sample_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        environments: Dict[str, SandboxEnvironment],
        interrupted: bool,
    ) -> None:
        if not interrupted:
            provider = get_ec2_instance_provider()
            for env in environments.values():
                if isinstance(env, Ec2SandboxEnvironment):
                    if provider is not None:
                        await provider.terminate_instance(env.instance_id, env.region)
                    else:
                        env.ec2_client.terminate_instances(
                            InstanceIds=[env.instance_id]
                        )
        return None

    @classmethod
    @override
    async def task_cleanup(
        cls,
        task_name: str,
        config: SandboxEnvironmentConfigType | None,
        cleanup: bool,
    ) -> None:
        return None

    @override
    async def exec(
        self,
        cmd: List[str],
        input: str | bytes | None = None,
        cwd: str | None = None,
        env: dict[str, str] = {},
        user: str | None = None,
        timeout: int | None = None,
        timeout_retry: bool = True,
    ) -> ExecResult[str]:
        if input is not None:
            self.logger.warning("Input parameter not supported by EC2 sandbox")

        if user is not None:
            self.logger.warning("User parameter not supported by EC2 sandbox")

        commands = []

        commands.extend(
            [f"export {shlex.quote(k)}={shlex.quote(v)}" for k, v in env.items()]
        )

        if cwd is not None:
            commands.append(f"cd {shlex.quote(cwd)}")

        commands.append(shlex.join(cmd))

        params: dict[str, Any] = {
            "commands": commands,
            "executionTimeout": [str(timeout or 3600)],
        }

        s3_key_prefix = self._s3_key_prefix("exec")

        return self._run_command(
            s3_key_prefix=s3_key_prefix, params=params, timeout=timeout
        )

    @classmethod
    @override
    async def cli_cleanup(cls, id: str | None) -> None:
        if id is None:
            provider = get_ec2_instance_provider()

            if provider is not None:
                region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", ""))
                sandbox_infos = await provider.find_sandbox_instances(region)
                await cls._cli_cleanup_provider(provider, sandbox_infos, region)
            else:
                await cls._cli_cleanup_default()
        else:
            print("\n[red]Cleanup by ID not implemented[/red]\n")

    @classmethod
    async def _cli_cleanup_provider(
        cls,
        provider: "Ec2InstanceProvider",
        instances: list[SandboxInstanceInfo],
        fallback_region: str,
    ) -> None:
        if not instances:
            print("\nNo EC2 sandbox instances found to clean up.\n")
            return

        vms_table = Table(
            box=box.SQUARE, show_lines=False,
            title_style="bold", title_justify="left",
        )
        vms_table.add_column("Instance ID")
        vms_table.add_column("Instance Name")
        for inst in instances:
            vms_table.add_row(inst.instance_id, inst.name)
        print(vms_table)

        if not cls._confirm_cleanup():
            return

        for inst in instances:
            region = inst.region or fallback_region
            await provider.terminate_instance(inst.instance_id, region)

    @classmethod
    async def _cli_cleanup_default(cls) -> None:
        ec2 = cls._get_session().client("ec2")
        response = ec2.describe_instances(
            Filters=[
                {
                    "Name": f"tag:{MARKER_TAG_KEY}",
                    "Values": ["true"],
                },
                {
                    "Name": "instance-state-name",
                    "Values": [
                        "pending",
                        "running",
                        "stopping",
                        "stopped",
                    ],
                },
            ]
        )
        instances = []
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                instances.append(instance)

        if not instances:
            print("\nNo EC2 sandbox instances found to clean up.\n")
            return

        vms_table = Table(
            box=box.SQUARE, show_lines=False,
            title_style="bold", title_justify="left",
        )
        vms_table.add_column("Instance ID")
        vms_table.add_column("Instance Name")
        for instance in instances:
            name_tag = ""
            if "Tags" in instance:
                for tag in instance["Tags"]:
                    if tag["Key"] == "Name":
                        name_tag = tag["Value"]
                        break
            vms_table.add_row(instance["InstanceId"], name_tag)
        print(vms_table)

        if not cls._confirm_cleanup():
            return

        instance_ids = [inst["InstanceId"] for inst in instances]
        ec2.terminate_instances(InstanceIds=instance_ids)

    @staticmethod
    def _confirm_cleanup() -> bool:
        # Borrowed from the proxmox provider - only prompt if in an interactive shell
        is_interactive_shell = sys.stdin.isatty()
        is_ci = "CI" in os.environ
        is_pytest = "PYTEST_CURRENT_TEST" in os.environ

        if is_interactive_shell and not is_ci and not is_pytest:
            if not Confirm.ask(
                "Are you sure you want to delete ALL the above resources?",
            ):
                print("Cancelled.")
                return False
        return True

    @classmethod
    def config_deserialize(cls, config: dict[str, Any]) -> BaseModel:
        return Ec2SandboxEnvironmentConfig(**config)

    def _delete_s3_object(self, key: str) -> None:
        """Delete an object from S3 after it's no longer needed."""
        try:
            self.s3_client.delete_object(Bucket=self.s3_bucket, Key=key)
            self.logger.debug(f"Deleted S3 object: {key}")
        except Exception as e:
            self.logger.warning(f"Failed to delete S3 object {key}: {e}")

    def _delete_s3_prefix(self, prefix: str) -> None:
        """Delete all objects with a given prefix from S3."""
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.s3_bucket, Prefix=prefix
            )
            if "Contents" in response:
                objects = [{"Key": obj["Key"]} for obj in response["Contents"]]
                if objects:
                    self.s3_client.delete_objects(
                        Bucket=self.s3_bucket, Delete={"Objects": objects}
                    )
                    self.logger.debug(f"Deleted S3 objects with prefix: {prefix}")
        except Exception as e:
            self.logger.warning(
                f"Failed to delete S3 objects with prefix {prefix}: {e}"
            )

    def _run_command(
        self, s3_key_prefix: str, params: dict[str, Any], timeout: int | None
    ) -> ExecResult:
        self.logger.debug(
            "send_command: instance=%s bucket=%s prefix=%s params=%s",
            self.instance_id, self.s3_bucket, s3_key_prefix, params,
        )
        # Send command using Session Manager with S3 output
        response = self.ssm_client.send_command(
            InstanceIds=[self.instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters=params,
            OutputS3BucketName=self.s3_bucket,
            OutputS3KeyPrefix=s3_key_prefix,
        )

        command_id = response["Command"]["CommandId"]
        self.logger.debug("send_command returned command_id=%s", command_id)

        stdout_key = (
            f"{s3_key_prefix}{command_id}/{self.instance_id}"
            "/awsrunShellScript/0.awsrunShellScript/"
            "stdout"
        )
        stderr_key = (
            f"{s3_key_prefix}{command_id}/{self.instance_id}"
            "/awsrunShellScript/0.awsrunShellScript/"
            "stderr"
        )
        self.logger.debug("stdout_key=%s stderr_key=%s", stdout_key, stderr_key)

        try:
            # Wait for command completion
            waiter = self.ssm_client.get_waiter("command_executed")

            waiter.wait(
                CommandId=command_id,
                InstanceId=self.instance_id,
                WaiterConfig={"Delay": 1, "MaxAttempts": timeout or 3600},
            )
            self.logger.debug("waiter completed for command_id=%s", command_id)

            # Get command output from S3

            stdout = self._read_s3_file_or_blank(
                stdout_key, limit_bytes=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
            )
            stderr = self._read_s3_file_or_blank(
                stderr_key, limit_bytes=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
            )
            self.logger.debug(
                "s3 read: stdout=%r (%d bytes) stderr=%r (%d bytes)",
                stdout[:200], len(stdout), stderr[:200], len(stderr),
            )

            # Still get return code from SSM API as it's not stored in S3
            output_response = self.ssm_client.get_command_invocation(
                CommandId=command_id, InstanceId=self.instance_id
            )
            return_code = output_response.get("ResponseCode", 0)

        except WaiterError as we:
            self.logger.debug(f"Command execution failed: {we}")

            if "Status" in we.last_response:
                self.logger.debug(f"Command status: {we.last_response['Status']}")
                if we.last_response["Status"] == "InProgress":
                    self.logger.warning("Command is still running, killing it.")
                    self.ssm_client.cancel_command(
                        CommandId=command_id, InstanceIds=[self.instance_id]
                    )
                    raise TimeoutError("Command execution timed out.")
            # Try to get output from S3 even on timeout/failure
            stdout = self._read_s3_file_or_blank(
                stdout_key, limit_bytes=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
            )
            stderr = self._read_s3_file_or_blank(
                stderr_key, limit_bytes=SandboxEnvironmentLimits.MAX_EXEC_OUTPUT_SIZE
            )
            return_code = (
                we.last_response.get("ResponseCode", 1) if we.last_response else 1
            )

            if "failed to run commands: exit status 126" in stderr:
                raise PermissionError(f"Permission denied executing command: {stderr}")

        self._delete_s3_object(stdout_key)
        self._delete_s3_object(stderr_key)
        self._delete_s3_prefix(f"{s3_key_prefix}{command_id}/")

        self.logger.debug(
            "ExecResult: rc=%s stdout=%d bytes stderr=%d bytes",
            return_code,
            len(stdout),
            len(stderr),
        )
        return ExecResult(
            success=return_code == 0,
            returncode=return_code,
            stdout=stdout,
            stderr=stderr,
        )

    def _get_s3_file_size(self, key: str) -> int:
        try:
            self.logger.debug("head_object: bucket=%s key=%s", self.s3_bucket, key)
            response = self.s3_client.head_object(Bucket=self.s3_bucket, Key=key)
            size = response.get("ContentLength", 0)
            self.logger.debug("head_object: key=%s size=%d", key, size)
            return size
        except ClientError as e:
            error_code = (
                e.response.get("Error", {}).get("Code")
                if e.response
                else None
            )
            self.logger.debug(
                "head_object error: key=%s code=%s", key, error_code
            )
            if error_code == "404":
                raise KeyError(key)
            else:
                raise

    def _read_s3_file_or_blank(self, key: str, limit_bytes: int) -> str:
        try:
            # Check file size before making the request with Range
            file_size = self._get_s3_file_size(key)

            if file_size >= limit_bytes:
                # File is larger than limit, use Range to get only what we need
                stdout_response = self.s3_client.get_object(
                    Bucket=self.s3_bucket,
                    Key=key,
                    Range=f"bytes=0-{limit_bytes - 1}",
                )
                response_body = stdout_response["Body"].read()
                raise OutputLimitExceededError(
                    limit_str="10 MiB", truncated_output=response_body.decode("utf-8")
                )
            else:
                # File is smaller than limit, get entire file without Range
                stdout_response = self.s3_client.get_object(
                    Bucket=self.s3_bucket, Key=key
                )
                response_body = stdout_response["Body"].read()
                return response_body.decode("utf-8")
        except (self.s3_client.exceptions.NoSuchKey, KeyError) as e:
            # When using SSM with S3 output, if the stdout or stderr are empty
            # it just doesn't create the S3 object, so we will see this error
            # in that case.
            # Unfortunately it makes it harder in the case of misconfiguration
            # of S3 permissions, since in that case we would also not have anything
            # in S3.
            self.logger.debug(
                "S3 key not found (returning empty): key=%s",
                key,
            )
            return ""

    @override
    async def read_file(  # type: ignore
        self, file: str, text: bool = True
    ) -> Union[str, bytes]:
        file_key = self._s3_key_prefix("read_file") + file

        url = self.s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.s3_bucket, "Key": file_key},
            ExpiresIn=60,
        )

        commands = ["#!/bin/sh", "set -e"]

        commands.append(
            shlex.join(["test", "-d", file]) + "&& echo 'Is a directory' 1>&2 && exit 1"
        )

        # for debugging purposes it might be helpful to add
        # "--fail-with-body"
        # here, but older versions of curl might not have it
        cmd = [
            "curl",
            "--fail",
            "--verbose",
            "--upload-file",
            file,
            url,
        ]

        commands.append(shlex.join(cmd))

        params: dict[str, Any] = {
            "commands": commands,
            "executionTimeout": ["3600"],
        }

        s3_key_prefix = self._s3_key_prefix("exec")

        result = self._run_command(
            s3_key_prefix=s3_key_prefix, params=params, timeout=3600
        )

        if not result.success:
            if "Is a directory" in str(result.stderr):
                raise IsADirectoryError(errno.EISDIR, "Is a directory", file)
            # It's a stretch to assert that we couldn't find the file if we
            # get to here: there could be other reasons for "not result.success".
            # TODO: improve this
            raise FileNotFoundError(f"Failed to read file {file}: {result.stderr}")

        # now download file from s3 with boto:
        try:
            # Check file size before making the request with Range
            file_size = self._get_s3_file_size(file_key)

            if file_size >= SandboxEnvironmentLimits.MAX_READ_FILE_SIZE:
                # File is larger than limit, use Range to get only what we need
                response = self.s3_client.get_object(
                    Bucket=self.s3_bucket,
                    Key=file_key,
                    Range=f"bytes=0-{SandboxEnvironmentLimits.MAX_READ_FILE_SIZE - 1}",
                )
                response_body = response["Body"].read()
                # match docker sandbox and do not include truncated output
                raise OutputLimitExceededError(
                    limit_str=SandboxEnvironmentLimits.MAX_READ_FILE_SIZE_STR,
                    truncated_output=None,
                )
            else:
                # File is smaller than limit, get entire file without Range
                response = self.s3_client.get_object(
                    Bucket=self.s3_bucket,
                    Key=file_key,
                )
                response_body = response["Body"].read()
        except (self.s3_client.exceptions.NoSuchKey, KeyError):
            raise FileNotFoundError(f"File {file} does not exist in S3 bucket.")

        # Clean up the S3 object
        self._delete_s3_object(file_key)

        if text:
            return response_body.decode("utf-8")
        else:
            return response_body

    @override
    async def write_file(self, file: str, contents: str | bytes) -> None:
        # ensure that the directory exists
        parent = Path(file).parent.as_posix()
        if parent != ".":
            result = await self.exec(["mkdir", "-p", parent])
            if not result.success:
                msg = f"Failed to create sandbox directory {parent}: {result.stderr}"
                raise RuntimeError(msg)

        file_key = self._s3_key_prefix("write_file") + file

        self.s3_client.put_object(
            Bucket=self.s3_bucket,
            Key=file_key,
            Body=contents,
        )

        url = self.s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.s3_bucket, "Key": file_key},
            ExpiresIn=60,
        )

        commands = ["#!/bin/sh", "set -e"]

        commands.append(
            shlex.join(["test", "-d", file]) + "&& echo 'Is a directory' 1>&2 && exit 1"
        )

        # for debugging purposes it might be helpful to add
        # "--fail-with-body"
        # here, but older versions of curl might not have it
        cmd = [
            "curl",
            "--fail",
            "--verbose",
            "--output",
            file,
            url,
        ]

        commands.append(shlex.join(cmd))

        params: dict[str, Any] = {
            "commands": commands,
            "executionTimeout": ["3600"],
        }

        s3_key_prefix = self._s3_key_prefix("write_file")

        result = self._run_command(
            s3_key_prefix=s3_key_prefix, params=params, timeout=3600
        )

        # Clean up the S3 object
        self._delete_s3_object(file_key)

        if result.success:
            self.logger.info(f"File {file} written successfully to EC2 instance.")

        if not result.success:
            if "is a directory" in result.stderr.casefold():
                raise IsADirectoryError(
                    f"Failed to write file: {file} because it is a directory already"
                )
            else:
                raise IOError(f"Failed to write file {file}: {result.stderr}")

    async def connection(self, *, user: str | None = None) -> SandboxConnection:
        return SandboxConnection(
            type="ec2",
            command=f"aws ssm start-session --target {self.instance_id} "
            "--document-name AWS-StartInteractiveCommand "
            '--parameters command="bash -l"',
        )

    def _s3_key_prefix(
        self, operation: Literal["read_file", "write_file", "exec"]
    ) -> str:
        rand = "".join(random.choices(string.ascii_letters + string.digits, k=8))
        timestamp = datetime.now().isoformat()
        return f"{self.s3_key_prefix}{operation}/{timestamp}-{rand}/"
