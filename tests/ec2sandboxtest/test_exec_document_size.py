"""Offline checks for SSM command-document rejection and normal execution."""

from contextlib import closing
from unittest import mock

import botocore.session
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment
from ec2sandbox._instance_provider import ProvisionedInstance


@pytest.fixture
def sandbox():
    ssm = mock.MagicMock()
    s3 = mock.MagicMock()
    ec2 = mock.MagicMock()
    clients = {"ssm": ssm, "s3": s3, "ec2": ec2}
    session = mock.MagicMock()
    session.client.side_effect = lambda service, **kwargs: clients[service]
    instance = ProvisionedInstance("i-synthetic", "eu-west-2", "bucket-synthetic")
    with mock.patch.object(Ec2SandboxEnvironment, "_get_session", return_value=session):
        return Ec2SandboxEnvironment(instance)


@pytest.mark.parametrize(
    ("cmd", "stdin"),
    [
        (["wc", "-c"], "a" * (128 * 1024)),
        (["wc", "-c"], b"\x00\xff" * (64 * 1024)),
        (["printf", "%s", "x" * (128 * 1024)], None),
        (["printf", "%s", "λ" * (64 * 1024)], None),
    ],
    ids=["text-input", "binary-input", "long-command", "unicode-command"],
)
async def test_exec_reports_a_catchable_document_size_error(sandbox, cmd, stdin):
    error = ClientError(
        {"Error": {"Code": "MaxDocumentSizeExceeded", "Message": "Too large"}},
        "SendCommand",
    )
    sandbox.ssm_client.send_command.side_effect = error

    with pytest.raises(ValueError, match="document-size limit") as caught:
        await sandbox.exec(cmd, input=stdin, cwd="/work dir", env={"MODE": "test"})

    assert caught.value.__cause__ is error
    assert "write_file()" in str(caught.value)
    assert "synthetic input" not in str(caught.value)
    sandbox.ssm_client.send_command.assert_called_once()
    sandbox.ssm_client.get_waiter.assert_not_called()
    sandbox.s3_client.assert_not_called()
    assert sandbox.s3_client.mock_calls == []


async def test_real_botocore_service_exception_is_translated(sandbox):
    # Stubber generates the service-specific exception without any AWS call.
    session = botocore.session.get_session()
    with closing(
        session.create_client(
            "ssm",
            region_name="eu-west-2",
            endpoint_url="https://ssm.example.invalid",
            aws_access_key_id="synthetic-key",
            aws_secret_access_key="synthetic-secret",
        )
    ) as client:
        sandbox.ssm_client = client
        with Stubber(client) as stubber:
            stubber.add_client_error(
                "send_command",
                service_error_code="MaxDocumentSizeExceeded",
                service_message="The total size exceeds the document limit.",
                http_status_code=400,
                expected_params={
                    "InstanceIds": ["i-synthetic"],
                    "DocumentName": "AWS-RunShellScript",
                    "Parameters": ANY,
                    "OutputS3BucketName": "bucket-synthetic",
                    "OutputS3KeyPrefix": ANY,
                },
            )
            with pytest.raises(ValueError, match="document-size limit") as caught:
                await sandbox.exec(["cat"], input="synthetic input")
            assert isinstance(
                caught.value.__cause__, client.exceptions.MaxDocumentSizeExceeded
            )
            stubber.assert_no_pending_responses()
    assert sandbox.s3_client.mock_calls == []


@pytest.mark.parametrize(
    "code",
    [
        "AccessDeniedException",
        "ThrottlingException",
        "InvalidInstanceId",
        "ValidationException",
    ],
)
async def test_other_service_errors_propagate_unchanged(sandbox, code):
    error = ClientError(
        {"Error": {"Code": code, "Message": "unchanged"}}, "SendCommand"
    )
    sandbox.ssm_client.send_command.side_effect = error
    with pytest.raises(ClientError) as caught:
        await sandbox.exec(["true"])
    assert caught.value is error
    sandbox.ssm_client.send_command.assert_called_once()
    sandbox.ssm_client.get_waiter.assert_not_called()
    assert sandbox.s3_client.mock_calls == []


async def test_normal_command_result_and_parameters_are_preserved(sandbox):
    sandbox.ssm_client.send_command.return_value = {"Command": {"CommandId": "cmd-id"}}
    sandbox.ssm_client.get_command_invocation.return_value = {"ResponseCode": 7}
    with mock.patch.object(
        sandbox, "_read_s3_file_or_blank", side_effect=["stdout", "stderr"]
    ):
        result = await sandbox.exec(
            ["printf", "%s", "two words"], input="hello", timeout=12
        )
    assert (result.success, result.returncode, result.stdout, result.stderr) == (
        False,
        7,
        "stdout",
        "stderr",
    )
    params = sandbox.ssm_client.send_command.call_args.kwargs["Parameters"]
    assert params == {
        "commands": ["printf %s aGVsbG8= | base64 -d | printf %s 'two words'"],
        "executionTimeout": ["12"],
    }
    sandbox.ssm_client.get_waiter.assert_called_once_with("command_executed")
    assert sandbox.s3_client.delete_object.call_count == 2


async def test_later_client_errors_are_not_misclassified(sandbox):
    sandbox.ssm_client.send_command.return_value = {"Command": {"CommandId": "cmd-id"}}
    error = ClientError(
        {"Error": {"Code": "MaxDocumentSizeExceeded", "Message": "later failure"}},
        "GetCommandInvocation",
    )
    with mock.patch.object(sandbox, "_read_s3_file_or_blank", side_effect=error):
        with pytest.raises(ClientError) as caught:
            await sandbox.exec(["true"])
    assert caught.value is error
