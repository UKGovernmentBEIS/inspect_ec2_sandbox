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

    def client(service, **kwargs):
        if service == "ec2":
            return ec2_client
        if service == "ssm":
            return ssm_client
        raise AssertionError(f"unexpected client: {service}")

    session = mock.MagicMock()
    session.client.side_effect = client

    return DefaultEc2InstanceProvider(config, session), ec2_client


@pytest.mark.asyncio
async def test_create_instance_no_volume_size_omits_block_device_mappings():
    provider, ec2_client = _make_provider_with_mocks(_make_config())

    await provider.create_instance(
        instance_type="t3a.micro",
        ami_id="ami-123",
        tags=[("Name", "x")],
    )

    kwargs = ec2_client.run_instances.call_args.kwargs
    assert "BlockDeviceMappings" not in kwargs
    ec2_client.describe_images.assert_not_called()


@pytest.mark.asyncio
async def test_create_instance_with_volume_size_sets_block_device_mappings():
    provider, ec2_client = _make_provider_with_mocks(_make_config(volume_size=100))

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


def _make_recording_provider():
    """Fake provider: records each create_instance call, distinct id per call."""
    from ec2sandbox._instance_provider import ProvisionedInstance

    provider = mock.MagicMock()
    provider.calls = []

    async def create_instance(**kwargs):
        provider.calls.append(kwargs)
        return ProvisionedInstance(
            instance_id=f"i-{len(provider.calls)}",
            region="eu-west-2",
            s3_bucket="bucket-1",
        )

    async def terminate_instance(instance_id, region):
        provider.terminated.append(instance_id)

    provider.terminated = []
    provider.create_instance = create_instance
    provider.terminate_instance = terminate_instance
    return provider


async def _sample_init_with(provider, config):
    from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=provider,
    ):
        return await Ec2SandboxEnvironment.sample_init(
            task_name="t", config=config, metadata={}
        )


@pytest.mark.asyncio
async def test_sample_init_single_default_when_no_names():
    provider = _make_recording_provider()
    envs = await _sample_init_with(provider, _make_config())

    assert list(envs.keys()) == ["default"]
    assert len(provider.calls) == 1
    # Name tag unchanged from the original single-instance behaviour.
    name_tag = dict(provider.calls[0]["tags"])["Name"]
    assert name_tag == "inspect_ec2_sandbox_t"


@pytest.mark.asyncio
async def test_sample_init_creates_one_instance_per_name():
    provider = _make_recording_provider()
    envs = await _sample_init_with(provider, _make_config(sandbox_names=("a", "b")))

    # First name is the default; order preserved.
    assert list(envs.keys()) == ["a", "b"]
    assert envs["a"].instance_id != envs["b"].instance_id
    assert len(provider.calls) == 2
    name_tags = sorted(dict(c["tags"])["Name"] for c in provider.calls)
    assert name_tags == ["inspect_ec2_sandbox_t_a", "inspect_ec2_sandbox_t_b"]


@pytest.mark.asyncio
async def test_sample_init_rolls_back_on_partial_failure():
    from ec2sandbox._instance_provider import ProvisionedInstance

    provider = mock.MagicMock()
    provider.terminated = []
    created_ids = []

    async def create_instance(**kwargs):
        name = dict(kwargs["tags"])["Name"]
        if name.endswith("_b"):
            raise RuntimeError("boom")
        created_ids.append("i-a")
        return ProvisionedInstance(
            instance_id="i-a", region="eu-west-2", s3_bucket="bucket-1"
        )

    async def terminate_instance(instance_id, region):
        provider.terminated.append(instance_id)

    provider.create_instance = create_instance
    provider.terminate_instance = terminate_instance

    with pytest.raises(RuntimeError, match="boom"):
        await _sample_init_with(provider, _make_config(sandbox_names=("a", "b")))

    # The instance that did come up must be terminated, not leaked.
    assert provider.terminated == created_ids == ["i-a"]


def test_sandbox_names_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        _make_config(sandbox_names=("a", "a"))


def test_sandbox_names_must_not_be_empty_strings():
    with pytest.raises(ValueError, match="empty"):
        _make_config(sandbox_names=("a", ""))
