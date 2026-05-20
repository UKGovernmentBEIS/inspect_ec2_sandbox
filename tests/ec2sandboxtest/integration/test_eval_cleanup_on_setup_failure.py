"""End-to-end: inspect_ai eval whose setup script fails.

This is the scenario PR #12 was filed for. inspect_ai's
``init_sandbox_environments_sample`` calls ``sample_cleanup(..., True)``
when the setup script raises. With our task_cleanup sweep in place, the
instance must be terminated by the end of the eval — not leaked.

Slow: ~3-5 minutes per run (real instance provisioning + cloud-init).
Run with ``-m req_aws``.
"""

from __future__ import annotations

import time

import pytest
from inspect_ai import Task, eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ModelOutput, get_model
from inspect_ai.scorer import includes
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash

from ec2sandbox._ec2_sandbox_environment import Ec2SandboxEnvironment

from .conftest import running_inspect_instances

pytestmark = pytest.mark.req_aws


@task
def _task_with_failing_setup() -> Task:
    return Task(
        dataset=[
            Sample(
                input="anything",
                target="42",
                setup="#!/usr/bin/env bash\nexit 17  # deliberately fail setup\n",
            ),
        ],
        solver=[basic_agent(tools=[bash()], message_limit=5)],
        scorer=includes(),
        sandbox="ec2",
    )


def test_eval_with_failing_setup_does_not_leak_instances() -> None:
    before = running_inspect_instances()

    eval_logs = eval(
        tasks=[_task_with_failing_setup()],
        model=get_model(
            "mockllm/model",
            custom_outputs=[
                ModelOutput.for_tool_call(
                    model="mockllm/model",
                    tool_name="submit",
                    tool_arguments={"answer": "42"},
                ),
            ],
        ),
        log_level="info",
        sandbox_cleanup=True,
    )

    assert len(eval_logs) == 1
    assert eval_logs[0].samples is not None and len(eval_logs[0].samples) == 1
    sample_error = eval_logs[0].samples[0].error
    assert sample_error is not None, "expected the setup script failure to be recorded"
    assert "exit status 17" in sample_error.message, (
        f"unexpected sample error: {sample_error.message}"
    )

    # Give EC2 a moment to reflect the termination triggered by task_cleanup.
    time.sleep(15)

    new_running = running_inspect_instances() - before
    assert not new_running, f"instances leaked after setup-failure eval: {new_running}"
    assert Ec2SandboxEnvironment._tracked_instances == set(), (
        f"tracker should be empty after eval; got "
        f"{Ec2SandboxEnvironment._tracked_instances}"
    )
