# Inspect EC2 Sandbox

## Purpose

This plugin for [Inspect](https://inspect.aisi.org.uk/) allows you to use virtual machines
as sandboxes, running within AWS EC2.

## Installing

Add this using [Poetry](https://python-poetry.org/)

```
poetry add git+ssh://git@github.com/UKGovernmentBEIS/inspect_ec2_sandbox.git
```

or in [uv](https://github.com/astral-sh/uv),

```
uv add git+ssh://git@github.com/UKGovernmentBEIS/inspect_ec2_sandbox.git
```

## AWS Infrastructure

This plugin depends on certain infrastructure existing already. See the [infra docs](infra/README.md) for a
reference CDK stack which you can use to create it. If you use the reference stack, you can skip reading
the rest of this section.

This plugin creates EC2 instances as necessary using the AWS API, so you do not need to create them yourself.

### VPC

You must have a VPC in which the EC2 instances will be created.

You can specify the subnet and security group.

Within the subnet and security group, instances *must* be able to connect to:

- SSM
- SSM Messages
- S3
- EC2 Messages

Incoming SSH access is not necessary (and is discouraged for security reasons.)

### S3

An S3 bucket is required for file transfer, and to record the results of SSM command invocations.

If desired, you can specify a key prefix such that all objects will be created below the prefix.

The plugin should clean up after itself; the bucket is not used for long-term storage.

### IAM

EC2 instances created by the plugin require an Instance Profile that allows:

- read/write access to the S3 bucket
- SSM access (the managed policy AmazonSSMManagedInstanceCore is adequate)

It's recommended not to exceed the above scope, since Inspect AI agents using the
sandbox will be able to invoke whatever AWS services are permitted.

## Amazon Machine Image (AMI)

The provider will use the latest Ubuntu 24.04 AMI by default.

Otherwise you can specify an AMI with the config parameter `ami_id` 
or the environment variable `INSPECT_EC2_SANDBOX_AMI_ID`. 

Your AMI must have the 
[AWS SSM agent](https://docs.aws.amazon.com/systems-manager/latest/userguide/ami-preinstalled-agent.html).

## Configuring evals

You can configure the eval using either environment variables, or by configuration in Python code, or both.
Configuration takes precedence over environment variables.

To make an eval portable it's recommended to set `ami_id` and `instance_type` in code,
and allow the end-user to specify the rest.

### Environment variables

The following environment variables must be set:

```bash
INSPECT_EC2_SANDBOX_REGION=eu-west-1
INSPECT_EC2_SANDBOX_VPC_ID=vpc-123456
INSPECT_EC2_SANDBOX_SECURITY_GROUP_ID=sg-56781234
INSPECT_EC2_SANDBOX_SUBNET_ID=subnet-654321
INSPECT_EC2_SANDBOX_INSTANCE_PROFILE=Ec2SandboxStack-SandboxInstanceProfile123-456
INSPECT_EC2_SANDBOX_S3_BUCKET=ec2sandboxstack-databucket123-456
```

The following environment variables are optional:

```bash
INSPECT_EC2_SANDBOX_AMI_ID=ami-123456
INSPECT_EC2_SANDBOX_INSTANCE_TYPE=t3a.small
INSPECT_EC2_SANDBOX_S3_KEY_PREFIX=sandbox-comms
INSPECT_EC2_SANDBOX_EXTRA_TAGS_STR='tagname1=tagvalue1;tagname2=tagvalue2'
```

### Configuration

As an alternative to the above environment variables you can specify the configuration directly in code, e.g

```python
sandbox=SandboxEnvironmentSpec("ec2", Ec2SandboxEnvironmentConfig.from_settings(
    region="eu-west-2",
    vpc_id="vpc-123456",
    security_group_id="sg-56781234",
    s3_bucket="ec2sandboxstack-databucket123-456",
    instance_profile="Ec2SandboxStack-SandboxInstanceProfile123-456",
    ami_id="ami-123456",
    subnet_id="subnet-654321",
    instance_type="t3a.small",
    extra_tags=(
        ("foo", "bar"),
    )
)),
```

See [schema.py](src/ec2sandbox/schema.py) for details.

## Tech Debt / Missing features

- task_cleanup is not implemented; only the default sample_cleanup is, so if you Ctrl-C a run, you have to clean up with the CLI command
- Move long-running AWS commands to a separate thread to avoid blocking Inspect's TUI
- Integration testing of all features
- Add more sample evals
- many timeouts are hard-coded
- better logging/tracing


## Developing

See [CONTRIBUTING.md](CONTRIBUTING.md)
