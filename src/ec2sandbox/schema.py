"""
Schema definitions for EC2 sandbox environments.

This module provides configuration classes and utility functions for defining
 Inspect EC2 sandbox environments.
"""

import os
from typing import Optional, Tuple

from pydantic import BaseModel, ConfigDict
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


class Ec2SandboxEnvironmentConfig(BaseModel):
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
        volume_size: Root EBS volume size in GiB (optional). If None, the
            AMI's baked-in size is used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Shared fields — used by both the direct-EC2 path and any custom
    # Ec2InstanceProvider. Have sensible defaults.
    instance_type: str = "t3a.large"
    # empty -> the provider chooses (DefaultEc2InstanceProvider resolves the
    # current Ubuntu 24.04 AMI on first create_instance call).
    ami_id: str = ""
    extra_tags: Tuple[Tuple[str, str], ...] = ()
    s3_key_prefix: str = ""
    volume_size: Optional[int] = None

    # Direct-EC2-path fields — required when no Ec2InstanceProvider is
    # registered, ignored otherwise. ``sample_init`` validates these at
    # call time when the direct path is taken.
    region: Optional[str] = None
    # TODO is vpc_id actually needed? We could just force a subnet ID.
    vpc_id: Optional[str] = None
    security_group_id: Optional[str] = None
    subnet_id: Optional[str] = None
    instance_profile: Optional[str] = None
    s3_bucket: Optional[str] = None

    @classmethod
    def from_settings(cls, session=None, **kwargs):
        """Create an instance from environment settings with optional overrides.

        Args:
            session: Deprecated and ignored. AMI resolution is now deferred to
                DefaultEc2InstanceProvider.create_instance, so from_settings no
                longer makes AWS calls. Accepted (and ignored) so existing
                callers passing a session don't break.
            **kwargs: Field-level overrides applied on top of env-var settings.
        """
        del session  # accepted for backwards compatibility only
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

        region = params["region"]
        if region is None:
            region = os.getenv("AWS_REGION")
        if not isinstance(region, str):
            raise ValueError(
                "Region must be specified either in settings,"
                f" or as an environment variable {env_prefix}REGION or AWS_REGION."
            )
        params["region"] = region

        # AMI resolution is deferred to DefaultEc2InstanceProvider.create_instance
        # so that callers who only need terminate/find don't pay for an SSM
        # AMI lookup just to construct a config.
        if params["ami_id"] is None:
            params["ami_id"] = ""

        if params["instance_type"] is None:
            params["instance_type"] = "t3a.large"

        s3_key_prefix = params["s3_key_prefix"]
        if s3_key_prefix is None:
            s3_key_prefix = ""
            params["s3_key_prefix"] = s3_key_prefix
        if isinstance(s3_key_prefix, str) and s3_key_prefix.startswith("/"):
            raise ValueError(
                f"S3 key prefix '{s3_key_prefix}' must not start with a '/'"
            )

        return cls(**params)
