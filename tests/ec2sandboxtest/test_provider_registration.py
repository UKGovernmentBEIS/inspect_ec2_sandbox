"""Provider resolution must not depend on inspect_ai entry-point load order.

inspect_ai loads entry points lazily and stops sweeping once the ``ec2``
sandboxenv is registered, so a provider registered via its own entry point can
be missed by import order and silently fall back to the default. _resolve_provider
sweeps before falling back to close that gap.
"""

from unittest import mock

import ec2sandbox._instance_provider as instance_provider
from ec2sandbox import set_ec2_instance_provider
from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment


def test_resolve_provider_sweeps_entry_points_before_default(monkeypatch):
    monkeypatch.setattr(instance_provider, "_provider", None)
    monkeypatch.setattr(Ec2SandboxEnvironment, "_providers_loaded", False)

    custom = mock.MagicMock()
    fake_ep = mock.MagicMock(value="custompkg.provider")
    fake_ep.load.side_effect = lambda: set_ec2_instance_provider(custom)
    monkeypatch.setattr(
        "ec2sandbox._ec2_sandbox_environment.entry_points",
        lambda group=None: [fake_ep],
    )

    provider, _ = Ec2SandboxEnvironment._resolve_provider(None)
    assert provider is custom
