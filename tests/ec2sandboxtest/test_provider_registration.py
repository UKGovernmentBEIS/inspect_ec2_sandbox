"""Provider resolution must not depend on inspect_ai entry-point load order.

inspect_ai loads its entry points lazily and skips the sweep once the ``ec2``
sandboxenv is registered. A custom Ec2InstanceProvider that registers via its
own entry point could therefore be missed depending on import order, silently
falling back to the direct-EC2 default provider. _resolve_provider sweeps the
entry points before falling back to close that gap.
"""

from unittest import mock

import ec2sandbox._instance_provider as instance_provider
from ec2sandbox import set_ec2_instance_provider
from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment


class _FakeEntryPoint:
    """Stands in for an inspect_ai entry point that registers a provider."""

    name = "custom-ec2-provider"
    value = "custompkg.provider"

    def __init__(self, provider):
        self._provider = provider

    def load(self):
        set_ec2_instance_provider(self._provider)


def test_resolve_provider_sweeps_entry_points_when_none_registered(monkeypatch):
    # No provider registered yet and the sweep hasn't run.
    monkeypatch.setattr(instance_provider, "_provider", None)
    monkeypatch.setattr(Ec2SandboxEnvironment, "_providers_loaded", False)

    custom_provider = mock.MagicMock()
    monkeypatch.setattr(
        "ec2sandbox._ec2_sandbox_environment.entry_points",
        lambda group=None: [_FakeEntryPoint(custom_provider)],
    )

    provider, _config = Ec2SandboxEnvironment._resolve_provider(None)

    assert provider is custom_provider


def test_resolve_provider_skips_sweep_when_already_registered(monkeypatch):
    already_registered = mock.MagicMock()
    monkeypatch.setattr(instance_provider, "_provider", already_registered)
    monkeypatch.setattr(Ec2SandboxEnvironment, "_providers_loaded", False)

    swept = False

    def _tracking_entry_points(group=None):
        nonlocal swept
        swept = True
        return []

    monkeypatch.setattr(
        "ec2sandbox._ec2_sandbox_environment.entry_points", _tracking_entry_points
    )

    provider, _config = Ec2SandboxEnvironment._resolve_provider(None)

    assert provider is already_registered
    assert swept is False
