from typing import Any
from unittest import mock

import pytest

from ec2sandbox._instance_provider import DefaultEc2InstanceProvider
from ec2sandbox.schema import Ec2SandboxEnvironmentConfig


def _make_config(**overrides: Any) -> Ec2SandboxEnvironmentConfig:
    defaults: dict[str, Any] = dict(
        instance_type="t3a.micro",
        ami_id="ami-123",
        region="eu-west-2",
        security_group_id="sg-1",
        subnet_id="subnet-1",
        instance_profile="profile-1",
        s3_bucket="bucket-1",
    )
    defaults.update(overrides)
    return Ec2SandboxEnvironmentConfig(**defaults)


def _make_provider_with_mocks(
    config: Ec2SandboxEnvironmentConfig,
) -> tuple[DefaultEc2InstanceProvider, mock.MagicMock]:
    ec2_client = mock.MagicMock()
    ec2_client.run_instances.return_value = {"Instances": [{"InstanceId": "i-abc"}]}

    ssm_client = mock.MagicMock()
    ssm_client.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "Online"}]
    }

    def client(service: str, **kwargs: Any) -> mock.MagicMock:
        if service == "ec2":
            return ec2_client
        if service == "ssm":
            return ssm_client
        raise AssertionError(f"unexpected client: {service}")

    session = mock.MagicMock()
    session.client.side_effect = client

    return DefaultEc2InstanceProvider(config, session), ec2_client


@pytest.mark.asyncio
async def test_missing_config_fields_fail_fast() -> None:
    """Missing config fields are all named, and no AWS client is built."""
    config = _make_config(subnet_id=None, s3_bucket=None)
    session = mock.MagicMock()
    provider = DefaultEc2InstanceProvider(config, session)

    with pytest.raises(ValueError) as excinfo:
        await provider.create_instance(
            instance_type="t3a.micro",
            ami_id="ami-123",
            tags=[("Name", "x")],
        )

    # Both missing fields are reported at once, not just the first found.
    assert "subnet_id" in str(excinfo.value)
    assert "s3_bucket" in str(excinfo.value)
    # Fail-fast: validation must reject before any AWS interaction.
    session.client.assert_not_called()


@pytest.mark.asyncio
async def test_tags_reach_run_instances() -> None:
    """A tag passed to create_instance appears in the run_instances call."""
    provider, ec2_client = _make_provider_with_mocks(_make_config())

    await provider.create_instance(
        instance_type="t3a.micro",
        ami_id="ami-123",
        tags=[("sentinel_key", "sentinel_value")],
    )

    tag_specs = ec2_client.run_instances.call_args.kwargs["TagSpecifications"]
    sentinel = {"Key": "sentinel_key", "Value": "sentinel_value"}
    assert sentinel in tag_specs[0]["Tags"]
