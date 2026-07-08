# Changelog

## Unreleased

- custom `Ec2InstanceProvider` must drop the `region` parameter from `find_sandbox_instances()`.
- `Ec2SandboxEnvironmentConfig.from_settings()` no longer accepts a `session` argument (use Ec2SandboxEnvironment.set_session()).
- Custom `Ec2InstanceProvider`s are now resolved regardless of entry-point import order.
- Interrupted samples (Ctrl-C, failed setup script) no longer leak EC2 instances.
- Failed instance creation (cloud-init / SSM timeout) no longer leaks the instance.
- remove --fail-with-body from curl to support older versions
