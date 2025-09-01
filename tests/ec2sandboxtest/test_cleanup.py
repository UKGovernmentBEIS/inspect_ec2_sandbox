from typing import Tuple

import boto3
from inspect_ai.util import SandboxEnvironment

from ec2sandbox._ec2_sandbox_environment import (
    MARKER_TAG_KEY,
    Ec2SandboxEnvironment,
    Ec2SandboxEnvironmentConfig,
)


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

    post_cleanup_instance_ids = _read_instance_ids()

    assert new_instance_id not in post_cleanup_instance_ids


async def test_cli_cleanup() -> None:
    _, _, new_instance_id = await _create_environment(task_name="test_cli_cleanup")

    await Ec2SandboxEnvironment.cli_cleanup(id=None)

    post_cleanup_instance_ids = _read_instance_ids()

    assert new_instance_id not in post_cleanup_instance_ids


async def _create_environment(
    task_name: str,
) -> Tuple[Ec2SandboxEnvironmentConfig, dict[str, SandboxEnvironment], str]:
    config = Ec2SandboxEnvironmentConfig.from_settings(instance_type="t3a.micro")

    envs = await Ec2SandboxEnvironment.sample_init(
        task_name=task_name,
        config=config,
        metadata={},
    )

    new_instance_id = envs["default"].instance_id

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
