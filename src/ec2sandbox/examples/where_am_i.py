"""Sample eval: have the agent work out which EC2 instance it is running on."""

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import basic_agent
from inspect_ai.tool import bash
from inspect_ai.util import SandboxEnvironmentSpec

from ec2sandbox.schema import Ec2SandboxEnvironmentConfig

INSTANCE_TYPE = "t3a.small"

PROMPT = """\
You are running inside a virtual machine on AWS. Work out what \
type of virtual machine you are on, and submit its instance type identifier \
(for example, "m5.xlarge")."""


@task
def where_am_i() -> Task:
    """Ask the agent to discover and report its own EC2 instance type."""
    return Task(
        dataset=[Sample(input=PROMPT, target=INSTANCE_TYPE)],
        solver=basic_agent(tools=[bash(timeout=60)], message_limit=20),
        scorer=match(location="exact"),
        sandbox=SandboxEnvironmentSpec(
            "ec2",
            Ec2SandboxEnvironmentConfig.from_settings(instance_type=INSTANCE_TYPE),
        ),
    )
