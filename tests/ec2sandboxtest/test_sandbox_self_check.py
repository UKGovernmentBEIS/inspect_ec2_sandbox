import hashlib
import subprocess
from typing import AsyncGenerator, List

import pytest
from inspect_ai.util._sandbox.self_check import self_check

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment
from ec2sandbox.schema import Ec2SandboxEnvironmentConfig


@pytest.fixture
async def ec2_sandbox_environment() -> AsyncGenerator[Ec2SandboxEnvironment, None]:
    config = Ec2SandboxEnvironmentConfig.from_settings()
    task_name = "unit_test"
    envs = await Ec2SandboxEnvironment.sample_init(
        task_name=task_name,
        config=config,
        metadata={},
    )
    assert "default" in envs
    assert isinstance(envs["default"], Ec2SandboxEnvironment)
    sandbox_environment = envs["default"]
    yield sandbox_environment
    await Ec2SandboxEnvironment.sample_cleanup(
        task_name=task_name, config=config, environments=envs, interrupted=False
    )


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


async def test_self_check(ec2_sandbox_environment) -> None:
    known_failures: List[str] = [
        # Tests that are never going to pass due to how SSM works:
        "test_read_file_not_allowed",  # user is root, so this doesn't work
        "test_exec_as_user",  # unsupported
        "test_exec_as_nonexistent_user",  # unsupported
        "test_write_text_file_without_permissions",  # user is root
        "test_write_binary_file_without_permissions",  # user is root
    ]

    return await check_results_of_self_check(ec2_sandbox_environment, known_failures)


async def check_results_of_self_check(sandbox_env, known_failures=[]):
    self_check_results = await self_check(sandbox_env)
    failures = []
    for test_name, result in self_check_results.items():
        if result is not True and test_name not in known_failures:
            failures.append(f"Test {test_name} failed: {result}")
    if failures:
        assert False, "There were some failures!!" + "\n".join(failures)
