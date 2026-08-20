import hashlib
import subprocess
from typing import AsyncGenerator

import pytest

# Pull the portable check functions into this module so pytest collects them as
# tests, each driven by the `sandbox_env` fixture below.
from inspect_ai.util._sandbox.self_check import *  # noqa: F401, F403

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment

pytestmark = pytest.mark.req_aws

# Known failures, applied as strict xfails in the sandbox_env fixture (keyed on
# the running check's function name). Strict means a check that starts passing
# is surfaced rather than silently ignored.
_XFAILS = {
    # SSM runs everything as root, so permission bits don't bite.
    "test_read_file_not_allowed": "user is root, so this doesn't work",
    "test_write_text_file_without_permissions": "user is root",
    "test_write_binary_file_without_permissions": "user is root",
    # Input and command are embedded in the SSM document, capped at 97KB.
    "test_exec_input_large": "SSM 97KB document limit, see #40",
    "test_exec_large_command": "SSM 97KB document limit, see #40",
}


@pytest.fixture(scope="module")
async def ec2_sandbox_environment() -> AsyncGenerator[Ec2SandboxEnvironment, None]:
    task_name = "unit_test"
    envs = await Ec2SandboxEnvironment.sample_init(
        task_name=task_name,
        config=None,
        metadata={},
    )
    assert "default" in envs
    assert isinstance(envs["default"], Ec2SandboxEnvironment)
    yield envs["default"]
    await Ec2SandboxEnvironment.sample_cleanup(
        task_name=task_name, config=None, environments=envs, interrupted=False
    )


@pytest.fixture
async def sandbox_env(
    request: pytest.FixtureRequest, ec2_sandbox_environment: Ec2SandboxEnvironment
) -> Ec2SandboxEnvironment:
    reason = _XFAILS.get(request.node.originalname)
    if reason is not None:
        request.node.add_marker(pytest.mark.xfail(reason=reason, strict=True))
    return ec2_sandbox_environment


async def test_exec_10mb_limit(ec2_sandbox_environment) -> None:
    i = pow(2, 20) * 10 - 1000  # 10 MiB - 1000
    print(f"Testing exec with {i} characters")
    exec_string = ["perl", "-E", "print 'a' x " + str(i)]

    expected = subprocess.run(exec_string, stdout=subprocess.PIPE).stdout.decode(
        "utf-8"
    )

    exec_result = await ec2_sandbox_environment.exec(exec_string, timeout=60)
    assert len(exec_result.stdout) == len(expected)
    assert exec_result.stdout == expected


async def test_write_file_large(ec2_sandbox_environment) -> None:
    file_contents = (
        b"a" * 128 * 1024
    )  # not huge but big enough to trip up some sandbox implementations
    md5 = hashlib.md5()
    md5.update(file_contents)
    expected_md5 = md5.hexdigest()
    await ec2_sandbox_environment.write_file("large_content.txt", file_contents)
    exec_result = await ec2_sandbox_environment.exec(["md5sum", "large_content.txt"])
    assert exec_result.stdout == f"{expected_md5}  large_content.txt\n"
