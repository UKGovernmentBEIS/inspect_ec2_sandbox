"""Shared fixtures for the AWS-backed cleanup integration tests."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import entry_points

import boto3
import pytest

from ec2sandbox._ec2_sandbox_environment import (
    MARKER_TAG_KEY,
    Ec2SandboxEnvironment,
    Ec2SandboxEnvironmentConfig,
)
from ec2sandbox._instance_provider import get_ec2_instance_provider


@pytest.fixture(scope="session", autouse=True)
def _load_inspect_entrypoints() -> None:
    """Force entry-point loading so any registered Ec2InstanceProvider is active.

    inspect_ai.eval() does this itself, but tests that bypass eval() (or
    that need the provider at fixture-setup time) must load them manually.
    """
    for ep in entry_points(group="inspect_ai"):
        ep.load()


@pytest.fixture(autouse=True)
def _reset_tracker() -> Iterator[None]:
    """Tracker is process-global; isolate tests from each other."""
    Ec2SandboxEnvironment._tracked_instances.clear()
    yield
    Ec2SandboxEnvironment._tracked_instances.clear()


@pytest.fixture
def ec2_config() -> Ec2SandboxEnvironmentConfig:
    overrides = {"instance_type": "t3a.micro"}
    if get_ec2_instance_provider() is not None:
        return Ec2SandboxEnvironmentConfig(**overrides)
    return Ec2SandboxEnvironmentConfig.from_settings(**overrides)


def instance_state(instance_id: str) -> str | None:
    """Return the EC2 state for an instance, or None if not found."""
    ec2 = boto3.client("ec2")
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    for r in resp["Reservations"]:
        for inst in r["Instances"]:
            return inst["State"]["Name"]
    return None


def wait_until_terminated(instance_id: str, timeout: int = 180) -> str | None:
    """Poll EC2 until the instance is terminated/shutting-down or timeout elapses."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        state = instance_state(instance_id)
        if state in (None, "terminated", "shutting-down"):
            return state
        time.sleep(5)
    return instance_state(instance_id)


def running_inspect_instances() -> set[str]:
    """Return IDs of all sandbox-tagged instances currently in pending/running state."""
    ec2 = boto3.client("ec2")
    resp = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{MARKER_TAG_KEY}", "Values": ["true"]},
            {
                "Name": "instance-state-name",
                "Values": ["pending", "running"],
            },
        ]
    )
    return {inst["InstanceId"] for r in resp["Reservations"] for inst in r["Instances"]}
