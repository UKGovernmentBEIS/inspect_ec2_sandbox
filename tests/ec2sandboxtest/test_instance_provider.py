from unittest import mock

import pytest

from ec2sandbox._instance_provider import DefaultEc2InstanceProvider
from ec2sandbox.schema import Ec2SandboxEnvironmentConfig


def _make_config(**overrides) -> Ec2SandboxEnvironmentConfig:
    defaults = dict(
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


def _make_provider_with_mocks(config):
    ec2_client = mock.MagicMock()
    ec2_client.run_instances.return_value = {"Instances": [{"InstanceId": "i-abc"}]}
    ec2_client.describe_images.return_value = {
        "Images": [{"RootDeviceName": "/dev/sda1"}]
    }
    waiter = mock.MagicMock()
    ec2_client.get_waiter.return_value = waiter

    ssm_client = mock.MagicMock()
    ssm_client.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "Online"}]
    }
    ssm_client.get_parameters.return_value = {"Parameters": [{"Value": "ami-resolved"}]}

    def client(service, **kwargs):
        if service == "ec2":
            return ec2_client
        if service == "ssm":
            return ssm_client
        raise AssertionError(f"unexpected client: {service}")

    session = mock.MagicMock()
    session.client.side_effect = client

    return DefaultEc2InstanceProvider(config, session), ec2_client, ssm_client


@pytest.mark.asyncio
async def test_create_instance_no_volume_size_omits_block_device_mappings():
    provider, ec2_client, _ = _make_provider_with_mocks(_make_config())

    await provider.create_instance(
        instance_type="t3a.micro",
        ami_id="ami-123",
        tags=[("Name", "x")],
    )

    kwargs = ec2_client.run_instances.call_args.kwargs
    assert "BlockDeviceMappings" not in kwargs
    ec2_client.describe_images.assert_not_called()


@pytest.mark.asyncio
async def test_create_instance_terminates_on_post_launch_failure():
    """If the post-launch wait raises, the partially-launched instance is terminated."""
    provider, ec2_client, _ = _make_provider_with_mocks(_make_config())

    waiter = ec2_client.get_waiter.return_value
    waiter.wait.side_effect = RuntimeError("instance-running timeout")

    with pytest.raises(RuntimeError, match="instance-running timeout"):
        await provider.create_instance(
            instance_type="t3a.micro",
            ami_id="ami-123",
            tags=[("Name", "x")],
        )

    ec2_client.terminate_instances.assert_called_once_with(InstanceIds=["i-abc"])


@pytest.mark.asyncio
async def test_create_instance_resolves_and_caches_ami_when_empty():
    """Empty ami_id triggers an Ubuntu lookup at create_instance time.

    The provider uses its configured session for the SSM call, and a second
    create in the same region hits the cache instead of SSM.
    """
    DefaultEc2InstanceProvider._ami_cache.clear()

    provider, ec2_client, ssm_client = _make_provider_with_mocks(
        _make_config(ami_id="")
    )

    for _ in range(2):
        await provider.create_instance(
            instance_type="t3a.micro",
            ami_id="",
            tags=[("Name", "x")],
        )

    assert ec2_client.run_instances.call_args.kwargs["ImageId"] == "ami-resolved"
    assert ssm_client.get_parameters.call_count == 1


@pytest.mark.asyncio
async def test_create_instance_with_volume_size_sets_block_device_mappings():
    provider, ec2_client, _ = _make_provider_with_mocks(_make_config(volume_size=100))

    await provider.create_instance(
        instance_type="t3a.micro",
        ami_id="ami-123",
        tags=[("Name", "x")],
        volume_size=100,
    )

    kwargs = ec2_client.run_instances.call_args.kwargs
    assert kwargs["BlockDeviceMappings"] == [
        {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": 100}}
    ]
    ec2_client.describe_images.assert_called_once_with(ImageIds=["ami-123"])


async def _call_sample_init(config):
    from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment
    from ec2sandbox._instance_provider import ProvisionedInstance

    fake_provider = mock.MagicMock()

    async def create_instance(**kwargs):
        create_instance.kwargs = kwargs
        return ProvisionedInstance(
            instance_id="i-xyz",
            region="eu-west-2",
            s3_bucket="bucket-1",
        )

    fake_provider.create_instance = create_instance

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=fake_provider,
    ):
        await Ec2SandboxEnvironment.sample_init(
            task_name="t", config=config, metadata={}
        )
    return create_instance.kwargs


@pytest.mark.asyncio
async def test_sample_init_passes_volume_size_to_provider():
    kwargs = await _call_sample_init(_make_config(volume_size=200))
    assert kwargs["volume_size"] == 200


@pytest.mark.asyncio
async def test_sample_init_omits_volume_size_when_unset():
    # Providers that pre-date the volume_size parameter must keep working,
    # so sample_init must not pass the kwarg at all when it is None.
    kwargs = await _call_sample_init(_make_config())
    assert "volume_size" not in kwargs
