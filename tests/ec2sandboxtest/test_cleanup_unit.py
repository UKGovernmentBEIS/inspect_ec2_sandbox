"""Mock-based unit tests for the sample_cleanup / task_cleanup behaviour."""

from __future__ import annotations

from unittest import mock

import pytest
from inspect_ai.util import SandboxEnvironment

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment
from ec2sandbox._instance_provider import ProvisionedInstance


@pytest.fixture(autouse=True)
def _clear_tracker():
    Ec2SandboxEnvironment._tracked_instances.clear()
    yield
    Ec2SandboxEnvironment._tracked_instances.clear()


class _FakeProvider:
    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.terminated: list[tuple[str, str]] = []
        self._fail_on = fail_on or set()
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> "_FakeProvider":
        self.enter_count += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exit_count += 1
        return None

    async def create_instance(self, **kwargs):  # pragma: no cover (per-test)
        raise NotImplementedError

    async def terminate_instance(self, instance_id: str, region: str) -> None:
        if instance_id in self._fail_on:
            raise RuntimeError(f"simulated terminate failure for {instance_id}")
        self.terminated.append((instance_id, region))

    async def find_sandbox_instances(self, region):  # pragma: no cover
        return []


async def _seed_environment(
    task_name: str,
    instance_id: str = "i-aaa",
    region: str = "eu-west-2",
) -> dict[str, SandboxEnvironment]:
    """Run sample_init through a fake provider and return its environments."""
    fake_provider = mock.MagicMock()

    async def create_instance(**kwargs):
        return ProvisionedInstance(
            instance_id=instance_id,
            region=region,
            s3_bucket="bucket-1",
        )

    fake_provider.create_instance = create_instance
    fake_provider.__aenter__ = mock.AsyncMock(return_value=fake_provider)
    fake_provider.__aexit__ = mock.AsyncMock(return_value=None)

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=fake_provider,
    ):
        return await Ec2SandboxEnvironment.sample_init(
            task_name=task_name, config=None, metadata={}
        )


async def test_sample_init_registers_instance_in_tracker():
    await _seed_environment("task_a", instance_id="i-111")
    assert Ec2SandboxEnvironment._tracked_instances == {("i-111", "eu-west-2")}


async def test_sample_cleanup_interrupted_leaves_tracker_intact():
    envs = await _seed_environment("task_a", instance_id="i-111")
    terminator = _FakeProvider()

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.sample_cleanup(
            task_name="task_a", config=None, environments=envs, interrupted=True
        )

    assert terminator.terminated == []
    assert Ec2SandboxEnvironment._tracked_instances == {("i-111", "eu-west-2")}


async def test_sample_cleanup_success_terminates_and_deregisters():
    envs = await _seed_environment("task_a", instance_id="i-111")
    terminator = _FakeProvider()

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.sample_cleanup(
            task_name="task_a", config=None, environments=envs, interrupted=False
        )

    assert terminator.terminated == [("i-111", "eu-west-2")]
    assert Ec2SandboxEnvironment._tracked_instances == set()


async def test_task_cleanup_sweeps_remaining_tracked_instances():
    """task_cleanup sweeps every tracked instance regardless of task_name.

    inspect_ai calls task_cleanup with task_name="shutdown", so the tracker
    cannot be keyed by task_name.
    """
    await _seed_environment("task_a", instance_id="i-111")
    await _seed_environment("task_b", instance_id="i-222")
    assert Ec2SandboxEnvironment._tracked_instances == {
        ("i-111", "eu-west-2"),
        ("i-222", "eu-west-2"),
    }

    terminator = _FakeProvider()
    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.task_cleanup(
            task_name="shutdown", config=None, cleanup=True
        )

    assert sorted(terminator.terminated) == [
        ("i-111", "eu-west-2"),
        ("i-222", "eu-west-2"),
    ]
    assert Ec2SandboxEnvironment._tracked_instances == set()


async def test_task_cleanup_respects_cleanup_false(capsys):
    await _seed_environment("task_a", instance_id="i-111")
    terminator = _FakeProvider()

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.task_cleanup(
            task_name="shutdown", config=None, cleanup=False
        )

    assert terminator.terminated == []
    # Tracker still gets cleared so a later eval in the same process doesn't
    # double-count these instances.
    assert Ec2SandboxEnvironment._tracked_instances == set()
    captured = capsys.readouterr()
    assert "no-sandbox-cleanup" in captured.out.lower()
    assert "inspect sandbox cleanup ec2" in captured.out


async def test_task_cleanup_with_no_tracked_instances_is_noop():
    terminator = _FakeProvider()

    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.task_cleanup(
            task_name="shutdown", config=None, cleanup=True
        )

    assert terminator.terminated == []


async def test_task_cleanup_continues_after_terminate_failure():
    await _seed_environment("task_a", instance_id="i-111")
    await _seed_environment("task_a", instance_id="i-222")

    terminator = _FakeProvider(fail_on={"i-111"})
    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.task_cleanup(
            task_name="shutdown", config=None, cleanup=True
        )

    # i-222 still terminated even though i-111 failed.
    assert ("i-222", "eu-west-2") in terminator.terminated
    assert Ec2SandboxEnvironment._tracked_instances == set()


async def test_sample_cleanup_only_deregisters_its_own_environments():
    """Two samples in flight; one's sample_cleanup must not affect the other."""
    envs_a = await _seed_environment("task_a", instance_id="i-aaa")
    await _seed_environment("task_a", instance_id="i-bbb")

    terminator = _FakeProvider()
    with mock.patch(
        "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
        return_value=terminator,
    ):
        await Ec2SandboxEnvironment.sample_cleanup(
            task_name="task_a", config=None, environments=envs_a, interrupted=False
        )

    assert terminator.terminated == [("i-aaa", "eu-west-2")]
    assert Ec2SandboxEnvironment._tracked_instances == {("i-bbb", "eu-west-2")}
