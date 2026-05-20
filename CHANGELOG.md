# Changelog

## Unreleased

- terminate EC2 instances even when a sample is interrupted (Ctrl-C, setup-script
  failure, etc.). Cleanup now happens in `task_cleanup` for interrupted samples,
  matching the docker / k8s / proxmox sandboxes. `--no-sandbox-cleanup` is
  respected as before.
- `DefaultEc2InstanceProvider.create_instance` now terminates the launched
  instance if the post-launch wait (instance-running / SSM-ready) fails, so a
  cloud-init or SSM-agent timeout no longer leaks an instance.
- remove --fail-with-body from curl to support older versions