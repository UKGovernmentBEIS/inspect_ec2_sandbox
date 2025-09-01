from setuptools import find_packages, setup

desc = (
    "Placeholder package only. Please install from source "
    "https://github.com/UKGovernmentBEIS/inspect_ec2_sandbox. An EC2 Sandbox "
    "Environment for Inspect."
)
setup(
    name="inspect-ec2-sandbox",
    version="0.0.1",
    description=desc,
    long_description=desc,
    author="UK AI Safety Institute",
    packages=find_packages(),
    url="https://github.com/UKGovernmentBEIS/inspect_ec2_sandbox",
)
