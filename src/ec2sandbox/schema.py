"""
Schema definitions for EC2 sandbox environments.

This module provides configuration classes and utility functions for defining
 Inspect EC2 sandbox environments.
"""

import os
from typing import Optional, Tuple

import boto3
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from ._unpack_tags import unpack_tags

env_prefix = "INSPECT_EC2_SANDBOX_"


class _Ec2ExistingInfraSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_prefix=env_prefix
    )
    region: Optional[str] = None
    vpc_id: Optional[str] = None
    security_group_id: Optional[str] = None
    subnet_id: Optional[str] = None
    ami_id: Optional[str] = None
    instance_type: Optional[str] = None
    instance_profile: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_key_prefix: Optional[str] = None
    extra_tags_str: Optional[str] = None  # in the format "key1=value1;key2=value2"


class Ec2SandboxEnvironmentConfig(BaseModel, frozen=True):
    """
    Configuration for an EC2 sandbox environment.

    Attributes:
        region: AWS region
        vpc_id: VPC ID
        security_group_id: Security group ID
        subnet_id: Subnet ID (optional, will be chosen at random otherwise)
        ami_id: AMI ID (optional, defaults to Ubuntu 24.04)
        instance_type: Type of EC2 instance to launch (optional, defaults to t3a.large)
        instance_profile: IAM instance profile for the EC2 instance,
            needs to be able to talk to SSM and read/write from the S3 bucket
        s3_bucket: S3 bucket for storing sandbox communications
        s3_key_prefix: S3 key prefix for sandbox communications (optional).
            Useful if you want to constrain the sandbox
            to a specific folder in the bucket
        extra_tags: tuple of 2-tuples of additional tags
    """

    region: str
    vpc_id: str  # TODO is this actually needed, we could just force a subnet ID
    security_group_id: str
    subnet_id: str
    ami_id: str
    instance_type: str
    instance_profile: str
    s3_bucket: str
    s3_key_prefix: str
    extra_tags: Tuple[Tuple[str, str], ...]

    @classmethod
    def from_settings(cls, **kwargs):
        """Create an instance from environment settings with optional overrides."""
        settings = _Ec2ExistingInfraSettings()

        params = {
            "region": settings.region,
            "vpc_id": settings.vpc_id,
            "security_group_id": settings.security_group_id,
            "subnet_id": settings.subnet_id,
            "ami_id": settings.ami_id,
            "instance_type": settings.instance_type,
            "instance_profile": settings.instance_profile,
            "s3_bucket": settings.s3_bucket,
            "s3_key_prefix": settings.s3_key_prefix,
            "extra_tags": unpack_tags(settings.extra_tags_str),
        }

        # Override with any provided kwargs
        params.update(kwargs)

        if params["region"] is None:
            aws_region = os.getenv("AWS_REGION")
            if aws_region is not None:
                params["region"] = aws_region
            else:
                raise ValueError(
                    "Region must be specified either in settings,"
                    f" or as an environment variable {env_prefix}REGION or AWS_REGION."
                )

        if params["ami_id"] is None:
            params["ami_id"] = _find_ami_ubu24(params["region"])

        if params["instance_type"] is None:
            params["instance_type"] = "t3a.large"

        if params["s3_key_prefix"] is None:
            params["s3_key_prefix"] = ""

        if params["s3_key_prefix"].startswith("/"):
            raise ValueError(
                f"S3 key prefix '{params['s3_key_prefix']}' must not start with a '/'"
            )

        return cls(**params)


def _find_ami_ubu24(region: str) -> str:
    ssm_client = boto3.client("ssm", region_name=region)

    # see https://documentation.ubuntu.com/aws/aws-how-to/instances/find-ubuntu-images/
    response = ssm_client.get_parameters(
        Names=[
            "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
        ]
    )

    if not response["Parameters"]:
        raise ValueError("Could not find Ubuntu 24.04 AMI ID in SSM Parameter Store")

    return response["Parameters"][0]["Value"]
