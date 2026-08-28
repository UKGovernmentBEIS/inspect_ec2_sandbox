import hashlib
import subprocess
from collections.abc import AsyncGenerator

import pytest
from inspect_ai.util._sandbox.self_check import *  # noqa: F401, F403

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment

pytestmark = pytest.mark.req_aws

_RUNS_AS_ROOT = "SSM runs commands as root, so permission bits are not enforced"
_SSM_DOCUMENT_LIMIT = (
    "the command and its input are embedded in the SSM document, "
    "which SSM caps at 97KB (see #40)"
)

_XFAILS = {
    "test_read_file_not_allowed": _RUNS_AS_ROOT,
    "test_write_text_file_without_permissions": _RUNS_AS_ROOT,
    "test_write_binary_file_without_permissions": _RUNS_AS_ROOT,
    "test_exec_input_large": _SSM_DOCUMENT_LIMIT,
    "test_exec_large_command": _SSM_DOCUMENT_LIMIT,
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
def sandbox_env(
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
