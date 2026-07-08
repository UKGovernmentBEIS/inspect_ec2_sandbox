# Changelog

## Unreleased

- `Ec2SandboxEnvironmentConfig.from_settings()` no longer accepts a `session` argument.
- Interrupted samples (Ctrl-C, failed setup script) no longer leak EC2 instances.
- Failed instance creation (cloud-init / SSM timeout) no longer leaks the instance.
- remove --fail-with-body from curl to support older versions
