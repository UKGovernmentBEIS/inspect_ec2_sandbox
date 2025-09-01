import os
from unittest import mock

import pytest

from ec2sandbox.schema import Ec2SandboxEnvironmentConfig

from .helpers import has_aws_creds


def env_vars_all() -> dict[str, str]:
    return {
        "INSPECT_EC2_SANDBOX_REGION": "eu-west-2",
        "INSPECT_EC2_SANDBOX_VPC_ID": "vpc-123",
        "INSPECT_EC2_SANDBOX_SECURITY_GROUP_ID": "sg-456",
        "INSPECT_EC2_SANDBOX_SUBNET_ID": "subnet-654321",
        "INSPECT_EC2_SANDBOX_AMI_ID": "ami-789",
        "INSPECT_EC2_SANDBOX_INSTANCE_PROFILE": "Ec2SandboxStack-SandboxInstanceProfile123-456",
        "INSPECT_EC2_SANDBOX_S3_BUCKET": "fake-bucket",
    }


@pytest.fixture()
def mock_settings_env_vars_all():
    with mock.patch.dict(
        os.environ,
        env_vars_all(),
    ):
        yield 0


@pytest.fixture()
def mock_settings_env_vars_no_ami_id():
    vars = env_vars_all()
    vars.pop("INSPECT_EC2_SANDBOX_AMI_ID")
    with mock.patch.dict(
        os.environ,
        vars,
    ):
        yield 0


def test_env_vars(mock_settings_env_vars_all):
    assert mock_settings_env_vars_all is not None
    config = Ec2SandboxEnvironmentConfig.from_settings()
    assert config.ami_id == "ami-789"


@pytest.mark.skipif(not has_aws_creds(), reason="No AWS credentials found")
def test_find_ami(mock_settings_env_vars_no_ami_id):
    assert mock_settings_env_vars_no_ami_id is not None
    config = Ec2SandboxEnvironmentConfig.from_settings()
    assert config.ami_id is not None
    assert config.ami_id.startswith("ami-")
    assert config.ami_id != "ami-789"  # sanity check test
