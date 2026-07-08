import os
from pathlib import Path
from unittest import mock

import pytest

from ec2sandbox.schema import Ec2SandboxEnvironmentConfig


@pytest.fixture(autouse=True)
def _run_outside_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep a contributor's local .env file out of pydantic-settings' reach."""
    monkeypatch.chdir(tmp_path)


def env_vars_all() -> dict[str, str]:
    # AMI_ID must always be present: from_settings resolves a missing AMI
    # via a real SSM lookup, which these tests must never reach.
    return {
        "INSPECT_EC2_SANDBOX_REGION": "eu-west-2",
        "INSPECT_EC2_SANDBOX_VPC_ID": "vpc-123",
        "INSPECT_EC2_SANDBOX_SECURITY_GROUP_ID": "sg-456",
        "INSPECT_EC2_SANDBOX_SUBNET_ID": "subnet-654321",
        "INSPECT_EC2_SANDBOX_AMI_ID": "ami-789",
        "INSPECT_EC2_SANDBOX_INSTANCE_PROFILE": "profile-1",
        "INSPECT_EC2_SANDBOX_S3_BUCKET": "fake-bucket",
    }


def test_kwarg_overrides_env_var() -> None:
    """Config kwargs take precedence over environment variables."""
    env_vars = env_vars_all()
    env_vars["INSPECT_EC2_SANDBOX_INSTANCE_TYPE"] = "t3a.small"
    with mock.patch.dict(os.environ, env_vars, clear=True):
        config = Ec2SandboxEnvironmentConfig.from_settings(instance_type="t3a.xlarge")
    assert config.instance_type == "t3a.xlarge"


def test_region_falls_back_to_aws_region() -> None:
    """AWS_REGION is used when INSPECT_EC2_SANDBOX_REGION is unset."""
    env_vars = env_vars_all()
    env_vars.pop("INSPECT_EC2_SANDBOX_REGION")
    env_vars["AWS_REGION"] = "eu-west-1"
    with mock.patch.dict(os.environ, env_vars, clear=True):
        config = Ec2SandboxEnvironmentConfig.from_settings()
    assert config.region == "eu-west-1"


def test_missing_region_raises_value_error() -> None:
    """With no region from any source, from_settings raises a clear error."""
    env_vars = env_vars_all()
    env_vars.pop("INSPECT_EC2_SANDBOX_REGION")
    with mock.patch.dict(os.environ, env_vars, clear=True):
        with pytest.raises(ValueError, match="Region must be specified"):
            Ec2SandboxEnvironmentConfig.from_settings()


def test_s3_key_prefix_leading_slash_raises() -> None:
    """A leading '/' in s3_key_prefix is rejected; normal values pass."""
    with mock.patch.dict(os.environ, env_vars_all(), clear=True):
        with pytest.raises(ValueError, match="must not start with"):
            Ec2SandboxEnvironmentConfig.from_settings(s3_key_prefix="/bad")
        config = Ec2SandboxEnvironmentConfig.from_settings(
            s3_key_prefix="sandbox-comms"
        )
    assert config.s3_key_prefix == "sandbox-comms"
