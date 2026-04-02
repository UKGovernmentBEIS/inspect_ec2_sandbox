"""Protocol and registry for EC2 instance lifecycle management.

The EC2 sandbox provider delegates instance creation, termination, and
discovery to an ``Ec2InstanceProvider``.  By default no custom provider
is registered and the sandbox falls back to direct boto3 EC2/SSM calls.

External packages (e.g. an organisational wrapper that routes through a
Lambda or other control plane) can register an alternative provider at
import time via :func:`set_ec2_instance_provider`.  The Inspect entry-
point mechanism ensures that such packages are imported before the
sandbox is used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


@dataclass
class ProvisionedInstance:
    """Result of provisioning an EC2 sandbox instance.

    Contains everything the :class:`Ec2SandboxEnvironment` needs for
    runtime operations (exec / read_file / write_file via SSM + S3).
    """

    instance_id: str
    region: str
    s3_bucket: str
    s3_key_prefix: str = ""


@dataclass
class SandboxInstanceInfo:
    """Metadata for a discovered sandbox instance (used by cli_cleanup)."""

    instance_id: str
    name: str
    region: str


@runtime_checkable
class Ec2InstanceProvider(Protocol):
    """Protocol for EC2 instance lifecycle management.

    The default code path calls the EC2 and SSM APIs directly using
    credentials available to the caller.  Implement this protocol and
    register it with :func:`set_ec2_instance_provider` to route instance
    lifecycle through a different control plane (e.g. an organisational
    Lambda / API).
    """

    async def create_instance(
        self,
        instance_type: str,
        ami_id: str,
        tags: list[tuple[str, str]],
    ) -> ProvisionedInstance:
        """Create an EC2 instance and wait until it is SSM-ready.

        Args:
            instance_type: EC2 instance type (e.g. ``"t3a.large"``).
            ami_id: AMI to launch.  May be empty if the provider
                resolves its own AMI.
            tags: Key/value pairs to apply to the instance.

        Returns:
            A :class:`ProvisionedInstance` with the runtime config
            needed by the sandbox environment.
        """
        ...

    async def terminate_instance(self, instance_id: str, region: str) -> None:
        """Terminate an EC2 instance."""
        ...

    async def find_sandbox_instances(self, region: str) -> list[SandboxInstanceInfo]:
        """Find running sandbox instances for interactive cleanup."""
        ...


# ---------------------------------------------------------------------------
# Module-level provider registry
# ---------------------------------------------------------------------------

_provider: Ec2InstanceProvider | None = None


def set_ec2_instance_provider(provider: Ec2InstanceProvider) -> None:
    """Register a custom :class:`Ec2InstanceProvider`.

    Call this at import time (e.g. from an ``inspect_ai`` entry-point
    module) to override the default direct-boto3 instance lifecycle.
    """
    global _provider
    if _provider is not None:
        _logger.warning(
            "Overriding existing Ec2InstanceProvider %r with %r",
            type(_provider).__name__,
            type(provider).__name__,
        )
    _provider = provider


def get_ec2_instance_provider() -> Ec2InstanceProvider | None:
    """Return the registered provider, or ``None`` for the default path."""
    return _provider
