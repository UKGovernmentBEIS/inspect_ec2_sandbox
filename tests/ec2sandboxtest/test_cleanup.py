import asyncio
from typing import Tuple

import boto3
import pytest
from inspect_ai.util import SandboxEnvironment

from ec2sandbox._ec2_sandbox_environment import (
    MARKER_TAG_KEY,
    Ec2SandboxEnvironment,
    Ec2SandboxEnvironmentConfig,
)
from ec2sandbox._instance_provider import get_ec2_instance_provider

pytestmark = pytest.mark.req_aws


async def test_cleanup():
    existing_instance_ids = _read_instance_ids()

    config, envs, new_instance_id = await _create_environment(task_name="test_cleanup")

    instance_ids = _read_instance_ids()

    new_instance_ids = instance_ids - existing_instance_ids

    print(f"{existing_instance_ids=}; {instance_ids=}; {new_instance_ids=}")

    assert len(new_instance_ids) == 1

    assert new_instance_id == list(new_instance_ids)[0]

    await Ec2SandboxEnvironment.sample_cleanup(
        task_name="test_cleanup",
        config=config,
        environments=envs,
        interrupted=False,
    )

    await asyncio.sleep(5)

    post_cleanup_instance_ids = _read_instance_ids()

    assert new_instance_id not in post_cleanup_instance_ids


async def test_cli_cleanup() -> None:
    _, _, new_instance_id = await _create_environment(task_name="test_cli_cleanup")

    await Ec2SandboxEnvironment.cli_cleanup(id=None)

    post_cleanup_instance_ids = _read_instance_ids()

    assert new_instance_id not in post_cleanup_instance_ids


async def test_volume_size_override() -> None:
    # Pick a size that's distinct from any provider default and from the
    # AMI's baked-in size, so the override is observable end-to-end.
    requested_size = 220
    config, envs, _ = await _create_environment(
        task_name="test_volume_size_override",
        volume_size=requested_size,
    )

    try:
        env = envs["default"]
        assert isinstance(env, Ec2SandboxEnvironment)
        # findmnt → root partition, PKNAME → parent disk, /sys/block/<d>/size
        # is in 512-byte sectors. Divide by 2**21 to convert to GiB.
        script = (
            "set -e; "
            "root_part=$(findmnt -no SOURCE /); "
            'disk=$(lsblk -no PKNAME "$root_part"); '
            'sectors=$(cat /sys/block/"$disk"/size); '
            "echo $((sectors / 2097152))"
        )
        result = await env.exec(["bash", "-c", script], timeout=60)
        assert result.success, f"failed to inspect disk size: {result.stderr}"
        observed_gib = int(result.stdout.strip())
        assert observed_gib == requested_size, (
            f"Expected root disk of {requested_size} GiB, observed {observed_gib} "
            f"GiB (stdout={result.stdout!r})"
        )
    finally:
        await Ec2SandboxEnvironment.sample_cleanup(
            task_name="test_volume_size_override",
            config=config,
            environments=envs,
            interrupted=False,
        )


async def _create_environment(
    task_name: str,
    volume_size: int | None = None,
) -> Tuple[Ec2SandboxEnvironmentConfig, dict[str, SandboxEnvironment], str]:
    # Match sample_init: resolve any entry-point-registered provider before
    # branching on it. inspect_ai.eval() sweeps entry points itself; these
    # tests bypass eval().
    Ec2SandboxEnvironment._ensure_providers_loaded()

    overrides: dict[str, object] = {"instance_type": "t3a.micro"}
    if volume_size is not None:
        overrides["volume_size"] = volume_size

    config = (
        Ec2SandboxEnvironmentConfig(**overrides)
        if get_ec2_instance_provider() is not None
        else Ec2SandboxEnvironmentConfig.from_settings(**overrides)
    )

    envs = await Ec2SandboxEnvironment.sample_init(
        task_name=task_name,
        config=config,
        metadata={},
    )

    default_env = envs["default"]
    assert isinstance(default_env, Ec2SandboxEnvironment)
    new_instance_id = default_env.instance_id

    return config, envs, new_instance_id


def _read_instance_ids() -> set[str]:
    ec2 = boto3.client("ec2")
    response = ec2.describe_instances(
        Filters=[
            {
                "Name": f"tag:{MARKER_TAG_KEY}",
                "Values": ["true"],
            },
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running", "stopping", "stopped"],
            },
        ]
    )
    instance_ids = set()
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            instance_ids.add(instance["InstanceId"])

    return instance_ids
