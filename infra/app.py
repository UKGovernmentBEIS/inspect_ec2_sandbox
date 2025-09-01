"""CDK application entry point for EC2 sandbox infrastructure.

This module initializes and configures the AWS CDK application that
deploys the EC2 Sandbox infrastructure stack.
"""
import aws_cdk as cdk
from ec2sandboxinfra.ec2sandbox_stack import Ec2SandboxStack

app = cdk.App()

core_stack = Ec2SandboxStack(
    app,
    "Ec2SandboxStack",
    tags={
        "Project": "ec2sandboxinfra",
    },
)

app.synth()
