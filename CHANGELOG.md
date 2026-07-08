# Changelog

## Unreleased

- load inspect_ai entry points before falling back to the default provider, so a custom Ec2InstanceProvider registered via a separate entry point is used regardless of import order
- remove --fail-with-body from curl to support older versions