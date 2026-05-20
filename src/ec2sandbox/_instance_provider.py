"""Protocol and registry for EC2 instance lifecycle management.

The EC2 sandbox provider delegates instance creation, termination, and
discovery to an ``Ec2InstanceProvider``. When no custom provider is
registered, :class:`DefaultEc2InstanceProvider` is used, which calls
EC2 and SSM directly via ``aiobotocore``.

External packages (e.g. an organisational wrapper that routes through a
Lambda or other control plane) can register an alternative provider at
import time via :func:`set_ec2_instance_provider`. The Inspect entry-
point mechanism ensures that such packages are imported before the
sandbox is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import boto3
from aiobotocore.session import AioSession
from aiobotocore.session import get_session as get_aio_session
from botocore.exceptions import ClientError  # noqa: F401  (re-exported for convenience)
from tenacity import retry, stop_after_attempt, wait_fixed

from ._unpack_tags import convert_tags_for_aws_interface

if TYPE_CHECKING:
    from .schema import Ec2SandboxEnvironmentConfig

_logger = logging.getLogger(__name__)


# Tag applied to every EC2 instance the sandbox provisions, so that the
# default provider's ``find_sandbox_instances`` can discover them.
MARKER_TAG_KEY = "inspect_sandbox"


@dataclass
class ProvisionedInstance:
    """Result of provisioning an EC2 sandbox instance.

    Contains everything the :class:`Ec2SandboxEnvironment` needs for
    runtime operations (exec / read_file / write_file via SSM + S3).
    """

    instance_id: str
    region: str
    s3_bucket: str
    s3_key_prefix: str = ""


@dataclass
class SandboxInstanceInfo:
    """Metadata for a discovered sandbox instance (used by cli_cleanup)."""

    instance_id: str
    name: str
    region: str


@runtime_checkable
class Ec2InstanceProvider(Protocol):
    """Protocol for EC2 instance lifecycle management.

    The default code path calls the EC2 and SSM APIs directly using
    credentials available to the caller.  Implement this protocol and
    register it with :func:`set_ec2_instance_provider` to route instance
    lifecycle through a different control plane (e.g. an organisational
    Lambda / API).

    Providers are async context managers: ``__aenter__`` is the place to
    initialize any loop-bound resources (e.g. ``aiobotocore`` / httpx
    clients, which bind to the running event loop) and ``__aexit__`` is
    where they should be released. The sandbox enters the provider in
    :meth:`Ec2SandboxEnvironment.task_init` and exits it in
    :meth:`Ec2SandboxEnvironment.task_cleanup`, so loop-bound clients
    have a well-defined lifetime tied to inspect_ai's task lifecycle.

    Sync providers (e.g. boto3-only) may implement ``__aenter__``/
    ``__aexit__`` as no-ops.
    """

    async def __aenter__(self) -> "Ec2InstanceProvider":
        """Initialize any loop-bound resources for the current event loop."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Release any resources acquired in ``__aenter__``."""
        ...

    async def create_instance(
        self,
        instance_type: str,
        ami_id: str,
        tags: list[tuple[str, str]],
        volume_size: int | None = None,
    ) -> ProvisionedInstance:
        """Create an EC2 instance and wait until it is SSM-ready.

        Args:
            instance_type: EC2 instance type (e.g. ``"t3a.large"``).
            ami_id: AMI to launch.  May be empty if the provider
                resolves its own AMI.
            tags: Key/value pairs to apply to the instance.
            volume_size: Optional override (GiB) for the root EBS
                volume size. ``None`` keeps the AMI's baked-in size.

        Returns:
            A :class:`ProvisionedInstance` with the runtime config
            needed by the sandbox environment.
        """
        ...

    async def terminate_instance(self, instance_id: str, region: str) -> None:
        """Terminate an EC2 instance."""
        ...

    async def find_sandbox_instances(self, region: str) -> list[SandboxInstanceInfo]:
        """Find running sandbox instances for interactive cleanup."""
        ...


# ---------------------------------------------------------------------------
# Module-level provider registry
# ---------------------------------------------------------------------------

_provider: Ec2InstanceProvider | None = None


def set_ec2_instance_provider(provider: Ec2InstanceProvider) -> None:
    """Register a custom :class:`Ec2InstanceProvider`.

    Call this at import time (e.g. from an ``inspect_ai`` entry-point
    module) to override the default direct-boto3 instance lifecycle.
    """
    global _provider
    if _provider is not None:
        _logger.warning(
            "Overriding existing Ec2InstanceProvider %r with %r",
            type(_provider).__name__,
            type(provider).__name__,
        )
    _provider = provider


def get_ec2_instance_provider() -> Ec2InstanceProvider | None:
    """Return the registered custom provider, or ``None`` for the default."""
    return _provider


def get_provider_session(
    provider: Ec2InstanceProvider | None,
) -> "boto3.Session | None":
    """Return a provider-supplied ``boto3.Session`` if one is available.

    Providers may optionally implement a ``get_session()`` method to
    supply the session used for all runtime AWS operations (SSM, S3,
    EC2) performed by the sandbox.  The method is called once per task
    during ``Ec2SandboxEnvironment.task_init``.

    Returns ``None`` if no provider is registered, the provider does not
    implement ``get_session``, or the provider returns ``None``.
    """
    if provider is None:
        return None
    get_session = getattr(provider, "get_session", None)
    if get_session is None:
        return None
    return get_session()


# ---------------------------------------------------------------------------
# Default direct-EC2 provider
# ---------------------------------------------------------------------------


async def _root_device_name(ec2_client: Any, ami_id: str) -> str:
    """Return the root device name (e.g. ``/dev/sda1``) for ``ami_id``."""
    resp = await ec2_client.describe_images(ImageIds=[ami_id])
    images = resp.get("Images", [])
    if not images:
        raise ValueError(f"AMI {ami_id} not found when resolving root device name")
    return images[0]["RootDeviceName"]


@retry(stop=stop_after_attempt(20), wait=wait_fixed(30))
async def _wait_for_ssm(instance_id: str, ssm_client: Any) -> bool:
    """Wait for the SSM agent to come online on the given instance."""
    resp = await ssm_client.describe_instance_information(
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


class DefaultEc2InstanceProvider:
    """Default :class:`Ec2InstanceProvider` using ``aiobotocore`` for EC2/SSM.

    Used by the EC2 sandbox when no custom provider has been registered.
    Reads the infrastructure fields (``region``, ``security_group_id``,
    etc.) from the supplied :class:`Ec2SandboxEnvironmentConfig` at the
    point they are needed — ``create_instance`` requires the full set,
    while ``terminate_instance`` and ``find_sandbox_instances`` only
    need the supplied ``region``.

    The ec2/ssm clients are loop-bound: they're created in
    ``__aenter__`` and closed in ``__aexit__``. Calling any async method
    before entering raises ``RuntimeError``.
    """

    def __init__(
        self,
        config: "Ec2SandboxEnvironmentConfig",
        session: boto3.Session,
    ) -> None:
        self._config = config
        # The sync boto3 session is still used by ``Ec2SandboxEnvironment``
        # for its runtime operations (SSM SendCommand, S3 reads/writes).
        # We don't convert those here; we only convert the provider's own
        # EC2/SSM control-plane calls to aiobotocore.
        self._session = session
        # Loop-bound state created in __aenter__:
        self._aio_session: AioSession | None = None
        self._ec2_cm: Any = None
        self._ssm_cm: Any = None
        self._ec2_client: Any = None
        self._ssm_client: Any = None

    async def __aenter__(self) -> "DefaultEc2InstanceProvider":
        cfg = self._config
        self._aio_session = get_aio_session()
        # Create both clients eagerly so a single ``__aexit__`` always
        # closes everything we opened.
        self._ec2_cm = self._aio_session.create_client(
            "ec2", region_name=cfg.region or None
        )
        self._ec2_client = await self._ec2_cm.__aenter__()
        self._ssm_cm = self._aio_session.create_client(
            "ssm", region_name=cfg.region or None
        )
        self._ssm_client = await self._ssm_cm.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        # Best-effort close in reverse order; one failure shouldn't
        # block the other.
        try:
            if self._ssm_cm is not None:
                await self._ssm_cm.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            self._ssm_cm = None
            self._ssm_client = None
            try:
                if self._ec2_cm is not None:
                    await self._ec2_cm.__aexit__(exc_type, exc_val, exc_tb)
            finally:
                self._ec2_cm = None
                self._ec2_client = None
                self._aio_session = None

    def _require_ec2(self) -> Any:
        if self._ec2_client is None:
            raise RuntimeError(
                "DefaultEc2InstanceProvider is not entered. Use "
                "'async with provider:' or rely on "
                "Ec2SandboxEnvironment.task_init to enter it."
            )
        return self._ec2_client

    def _require_ssm(self) -> Any:
        if self._ssm_client is None:
            raise RuntimeError(
                "DefaultEc2InstanceProvider is not entered. Use "
                "'async with provider:' or rely on "
                "Ec2SandboxEnvironment.task_init to enter it."
            )
        return self._ssm_client

    def get_session(self) -> boto3.Session:
        return self._session

    async def create_instance(
        self,
        instance_type: str,
        ami_id: str,
        tags: list[tuple[str, str]],
        volume_size: int | None = None,
    ) -> ProvisionedInstance:
        cfg = self._config
        required = {
            "region": cfg.region,
            "security_group_id": cfg.security_group_id,
            "subnet_id": cfg.subnet_id,
            "instance_profile": cfg.instance_profile,
            "s3_bucket": cfg.s3_bucket,
            "ami_id": ami_id,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                "Ec2SandboxEnvironmentConfig is missing fields required by "
                f"the default EC2 instance provider: {', '.join(missing)}. "
                "Either populate them (typically via from_settings) or "
                "register an Ec2InstanceProvider."
            )

        ec2 = self._require_ec2()
        ssm = self._require_ssm()

        instance_params: dict[str, Any] = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "SecurityGroupIds": [cfg.security_group_id],
            "SubnetId": cfg.subnet_id,
            "TagSpecifications": convert_tags_for_aws_interface(
                "instance", tuple(tags)
            ),
            "IamInstanceProfile": {"Name": cfg.instance_profile},
        }
        if volume_size is not None:
            instance_params["BlockDeviceMappings"] = [
                {
                    "DeviceName": await _root_device_name(ec2, ami_id),
                    "Ebs": {"VolumeSize": volume_size},
                }
            ]
        response = await ec2.run_instances(**instance_params, MinCount=1, MaxCount=1)
        instance = response["Instances"][0]
        instance_id = instance["InstanceId"]

        # If anything between here and a successful return raises, we own
        # an instance that the sandbox layer will never see — terminate it
        # so the caller doesn't have to know.
        try:
            waiter = ec2.get_waiter("instance_running")
            await waiter.wait(InstanceIds=[instance_id])

            await _wait_for_ssm(instance_id, ssm)
        except BaseException:
            try:
                await ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception as cleanup_err:
                _logger.warning(
                    "Failed to terminate %s after create_instance error: %s",
                    instance_id,
                    cleanup_err,
                )
            raise

        assert cfg.region is not None  # validated above
        assert cfg.s3_bucket is not None  # validated above
        return ProvisionedInstance(
            instance_id=instance_id,
            region=cfg.region,
            s3_bucket=cfg.s3_bucket,
            s3_key_prefix=cfg.s3_key_prefix,
        )

    async def terminate_instance(self, instance_id: str, region: str) -> None:
        ec2 = self._require_ec2()
        await ec2.terminate_instances(InstanceIds=[instance_id])

    async def find_sandbox_instances(self, region: str) -> list[SandboxInstanceInfo]:
        ec2 = self._require_ec2()
        response = await ec2.describe_instances(
            Filters=[
                {"Name": f"tag:{MARKER_TAG_KEY}", "Values": ["true"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        results: list[SandboxInstanceInfo] = []
        for reservation in response["Reservations"]:
            for instance in reservation["Instances"]:
                name = ""
                for tag in instance.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break
                results.append(
                    SandboxInstanceInfo(
                        instance_id=instance["InstanceId"],
                        name=name,
                        region=region,
                    )
                )
        return results
