# Changelog

## Unreleased

- terminate EC2 instances even when a sample is interrupted (Ctrl-C, setup-script
  failure, etc.). Cleanup now happens in `task_cleanup` for interrupted samples,
  matching the docker / k8s / proxmox sandboxes. `--no-sandbox-cleanup` is
  respected as before.
- **Protocol change**: `Ec2InstanceProvider` is now an async context manager.
  `__aenter__` is the place to initialize any loop-bound resources
  (`aiobotocore` / `httpx` clients, which bind to the running event loop) and
  `__aexit__` releases them. `Ec2SandboxEnvironment.task_init` enters the
  registered provider and `task_cleanup` exits it, giving loop-bound clients
  a lifetime tied to inspect_ai's task lifecycle. Sync providers may
  implement both as no-ops.
- `DefaultEc2InstanceProvider` now uses `aiobotocore` for its EC2/SSM
  control-plane calls so it doesn't block the event loop. Clients are
  created in `__aenter__` and closed in `__aexit__`.
- `DefaultEc2InstanceProvider.create_instance` terminates the launched
  instance if the post-launch wait (instance-running / SSM-ready) fails, so a
  cloud-init or SSM-agent timeout no longer leaks an instance.
- remove --fail-with-body from curl to support older versions
