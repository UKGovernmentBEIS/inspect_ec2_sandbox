"""Tests that the package logger is opted in so its records reach the .eval.

Inspect leaves third-party package loggers at the root WARNING default, so
without the promotion in _ec2_sandbox_environment the sandbox's INFO records
are dropped before Inspect's handler ever sees them.
"""

from __future__ import annotations

import logging
from unittest import mock

import pytest

from ec2sandbox import _ec2_sandbox_environment
from ec2sandbox._instance_provider import ProvisionedInstance
from ec2sandbox.schema import Ec2SandboxEnvironmentConfig

PACKAGE_LOGGER = "ec2sandbox"
MODULE_LOGGER = "ec2sandbox._ec2_sandbox_environment"


def test_package_logger_enabled_for_info() -> None:
    """INFO is enabled at the default log level, so the .eval captures it."""
    assert logging.getLogger(PACKAGE_LOGGER).isEnabledFor(logging.INFO)


async def test_sample_init_logs_instance_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """sample_init records which instance ran the sample, and on what."""
    fake_provider = mock.MagicMock()

    async def create_instance(**kwargs):
        return ProvisionedInstance(
            instance_id="i-log",
            region="eu-west-2",
            s3_bucket="bucket-1",
        )

    fake_provider.create_instance = create_instance
    sandbox = _ec2_sandbox_environment.Ec2SandboxEnvironment

    with (
        mock.patch(
            "ec2sandbox._ec2_sandbox_environment.get_ec2_instance_provider",
            return_value=fake_provider,
        ),
        caplog.at_level(logging.INFO, logger=MODULE_LOGGER),
    ):
        try:
            await sandbox.sample_init(
                task_name="logging-task",
                config=Ec2SandboxEnvironmentConfig(
                    instance_type="t3a.micro", ami_id="ami-123"
                ),
                metadata={},
            )
        finally:
            sandbox._tracked_instances.clear()

    records = [r for r in caplog.records if r.message.startswith("sample_init:")]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    for fragment in (
        "provider=MagicMock",
        "type=t3a.micro",
        "ami=ami-123",
        "id=i-log",
        "region=eu-west-2",
    ):
        assert fragment in records[0].message
