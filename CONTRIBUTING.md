# Contributing Guide

**NOTE:** If you have any feature requests or suggestions, we'd love to hear about them
and discuss them with you before you raise a PR. Please come discuss your ideas with us
in our [Inspect
Community](https://join.slack.com/t/inspectcommunity/shared_invite/zt-2w9eaeusj-4Hu~IBHx2aORsKz~njuz4g)
Slack workspace.

## Before you open a PR

This provider is a thin layer over infrastructure we don't control — the EC2, SSM and S3
APIs. Most bugs in it are claims about what that infrastructure does at runtime, and
those are cheap to get wrong from reading documentation or upstream source.

If your change asserts a runtime behaviour, we need the observation, not the derivation.

- **Run it against real AWS and paste what you saw.** The `req_aws` marker identifies
  the tests that need credentials. A test without that marker is not evidence about
  runtime behaviour, however green it is.
- **Show the negative control.** Give the result with your change and without it. If you
  can't produce a run that fails on `main` and passes on your branch, say so and explain
  why.
- **If you can't run it, open an issue rather than a PR.** Reasoning, upstream source
  links and a proposed patch are all welcome in an issue. A confidently argued wrong
  premise costs more than no PR at all, because it has to be disproved before it can be
  declined.

The trap specific to this repo is that nothing reaches the sandbox VM directly: control
goes through the EC2 and SSM APIs, data through S3 (see Architecture below). Patching
boto3 establishes nothing about how SSM actually behaves — its timing, its truncation
limits, the shape of its errors. Instance creation is also pluggable
(`Ec2InstanceProvider`), so a run against one provider says little about another; say
which one you ran.

A sufficient experiment looks like: baseline without the change, the behaviour with it,
then baseline again to show the effect went away — several attempts per phase, plus a
positive control proving the test could have observed a difference if there were one.

## Getting started

This project uses [uv](https://github.com/astral-sh/uv) for Python packaging.

Run this beforehand:

```
uv sync
```

The commands below are written as `uv run ...`, which works whether or not the venv is
activated. Drop the prefix if you'd rather activate it:

```
source .venv/bin/activate
```

## Architecture

The diagram below shows the high-level architecture of `inspect_ec2_sandbox` and how
it interacts with AWS. The package runs in-process inside Inspect and is split into
two main responsibilities:

- **Instance provisioning** (`_instance_provider.py`) — uses the EC2 API (`run_instances`)
  to create sandbox VMs, and SSM (`describe_instance_information`) to wait for the SSM
  agent to come online.
- **Sandbox RPC** (`_ec2_sandbox_environment.py`) — implements Inspect's `SandboxEnvironment`
  interface (`exec`, `read_file`, `write_file`). Commands are dispatched via SSM
  `send_command` with stdout/stderr written to S3; file transfers happen via S3 presigned
  URLs that the sandbox VM fetches with `curl`.

The Inspect host never opens a network connection to the sandbox VM directly: all control
flows through the AWS control plane (EC2 + SSM) and all data flows through S3.

![Architecture](docs/architecture.drawio.png)

## Testing

Run the tests with:

```bash
uv run pytest
```

For most of the tests you will need AWS credentials to be available to the boto
python library. To skip those:

```bash
uv run pytest -m "not req_aws"
```

## Linting & Formatting

[Ruff](https://docs.astral.sh/ruff/) is used for linting and formatting. To run both
checks manually:

```bash
uv run ruff check .
uv run ruff format .
```

## Type Checking

[Mypy](https://github.com/python/mypy) is used for type checking. To run type checks
manually:

```bash
uv run mypy
```

## Changelog

If appropriate, add an entry under the `## Unreleased` heading in `CHANGELOG.md` when
submitting a PR. Create that heading if the last release consumed it.

Entries under a dated release heading are published history — don't add to or edit
them. In particular, if a release is cut after you branch, a stale branch can silently
land your entry in the just-released section (the release commit renames `##
Unreleased` to the dated heading, so your diff still applies): after rebasing onto
`main`, check your entry still sits under `## Unreleased`.

## Conventions

### Package Structure and API Visibility

The Python packages, modules and members follow a similar API visibility naming
convention to that used in the [inspect_ai](https://inspect.aisi.org.uk/) package.

Public API members (e.g. classes, functions, constants) are exported in the package's
`__init__.py` file. Members are exported rather than modules (i.e. .py files) to avoid
all of the module's imports also being implicitly exported.

Module-private members are prefixed with an underscore `_`. These members are not
intended for use outside of the module in which they are defined (except in tests).

Class-private members are prefixed with an underscore `_`. These members are not
intended for use outside of the class in which they are defined (except in tests). We
don't use double underscores `__`  which is consistent with [Google's Python style
guide](https://google.github.io/styleguide/pyguide.html).

Non-public modules (i.e. .py files) are prefixed with an underscore `_` (unless a parent
package is already prefixed with an underscore).
