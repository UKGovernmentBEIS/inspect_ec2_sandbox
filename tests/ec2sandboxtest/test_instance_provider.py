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


def _mock_aiobotocore() -> tuple[mock.AsyncMock, mock.AsyncMock, mock.MagicMock]:
    """Build (ec2_client, ssm_client, aio_session) mocks for the new aiobotocore path.

    aiobotocore returns clients via ``async with session.create_client(...)``.
    The clients themselves have ``await``-able API methods. ``get_waiter`` is
    sync but the waiter's ``wait`` is async.
    """
    ec2_client = mock.AsyncMock()
    ec2_client.run_instances.return_value = {"Instances": [{"InstanceId": "i-abc"}]}
    ec2_client.describe_images.return_value = {
        "Images": [{"RootDeviceName": "/dev/sda1"}]
    }
    waiter = mock.MagicMock()
    waiter.wait = mock.AsyncMock()
    ec2_client.get_waiter = mock.MagicMock(return_value=waiter)

    ssm_client = mock.AsyncMock()
    ssm_client.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "Online"}]
    }

    def create_client(service: str, **kwargs):
        cm = mock.MagicMock()
        if service == "ec2":
            cm.__aenter__ = mock.AsyncMock(return_value=ec2_client)
        elif service == "ssm":
            cm.__aenter__ = mock.AsyncMock(return_value=ssm_client)
        else:
            raise AssertionError(f"unexpected client: {service}")
        cm.__aexit__ = mock.AsyncMock(return_value=None)
        return cm

    aio_session = mock.MagicMock()
    aio_session.create_client.side_effect = create_client
    return ec2_client, ssm_client, aio_session


@pytest.fixture
def aio_mocks():
    ec2_client, ssm_client, aio_session = _mock_aiobotocore()
    with mock.patch(
        "ec2sandbox._instance_provider.get_aio_session",
        return_value=aio_session,
    ):
        yield ec2_client, ssm_client


def _make_provider(config):
    # The sync boto3 session is still kept by the provider (for ``get_session()``)
    # but the aiobotocore client comes from the patched get_aio_session.
    return DefaultEc2InstanceProvider(config, mock.MagicMock())


@pytest.mark.asyncio
async def test_create_instance_no_volume_size_omits_block_device_mappings(aio_mocks):
    ec2_client, _ = aio_mocks
    async with _make_provider(_make_config()) as provider:
        await provider.create_instance(
            instance_type="t3a.micro",
            ami_id="ami-123",
            tags=[("Name", "x")],
        )

    kwargs = ec2_client.run_instances.call_args.kwargs
    assert "BlockDeviceMappings" not in kwargs
    ec2_client.describe_images.assert_not_called()


@pytest.mark.asyncio
async def test_create_instance_terminates_on_post_launch_failure(aio_mocks):
    """If the post-launch wait raises, the partially-launched instance is terminated."""
    ec2_client, _ = aio_mocks
    waiter = ec2_client.get_waiter.return_value
    waiter.wait.side_effect = RuntimeError("instance-running timeout")

    async with _make_provider(_make_config()) as provider:
        with pytest.raises(RuntimeError, match="instance-running timeout"):
            await provider.create_instance(
                instance_type="t3a.micro",
                ami_id="ami-123",
                tags=[("Name", "x")],
            )

    ec2_client.terminate_instances.assert_awaited_once_with(InstanceIds=["i-abc"])


@pytest.mark.asyncio
async def test_create_instance_with_volume_size_sets_block_device_mappings(aio_mocks):
    ec2_client, _ = aio_mocks
    async with _make_provider(_make_config(volume_size=100)) as provider:
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
    ec2_client.describe_images.assert_awaited_once_with(ImageIds=["ami-123"])


@pytest.mark.asyncio
async def test_create_instance_requires_entered_provider():
    """Calling create_instance on an un-entered provider must error cleanly."""
    provider = _make_provider(_make_config())
    with pytest.raises(RuntimeError, match="not entered"):
        await provider.create_instance(
            instance_type="t3a.micro",
            ami_id="ami-123",
            tags=[("Name", "x")],
        )


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
    fake_provider.__aenter__ = mock.AsyncMock(return_value=fake_provider)
    fake_provider.__aexit__ = mock.AsyncMock(return_value=None)

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
