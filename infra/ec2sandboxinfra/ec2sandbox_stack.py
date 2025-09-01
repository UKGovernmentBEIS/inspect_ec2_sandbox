"""Module for defining the Inspect EC2 Sandbox infrastructure using AWS CDK.

This module contains the CDK stack definition for creating isolated
Inspect EC2 sandboxes with private VPC, security groups,
and necessary IAM permissions.

It does not define any EC2 instances itself; these
are created using RunInstances in the sandbox provider.
"""

import aws_cdk as cdk
import aws_cdk.aws_ec2 as ec2
import aws_cdk.aws_iam as iam
import aws_cdk.aws_s3 as s3
from constructs import Construct


class Ec2SandboxStack(cdk.Stack):
    """CDK Stack for creating Inspect EC2 sandboxes.

    Creates necessary infrastructure.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        **kwargs,
    ) -> None:
        """Initialize the EC2 sandbox infrastructure stack.

        Args:
            scope: The parent construct.
            id: The construct ID.
            **kwargs: Additional keyword arguments passed to the parent Stack.
        """
        super().__init__(scope, id, **kwargs)

        # VPC with single AZ, two private subnets - one isolated, one with internet access
        # Enable DNS hostnames and resolution for S3 gateway endpoint
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=1,
            nat_gateways=1,  # NAT gateway for internet access
            enable_dns_hostnames=True,
            enable_dns_support=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PrivateIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=22,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateWithNat",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=22,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=22,
                ),
            ],
        )

        # Get both private subnets
        private_isolated_subnet = self.vpc.isolated_subnets[0]
        private_with_internet_subnet = self.vpc.private_subnets[0]

        # S3 Gateway Endpoint (free) - associate with both private subnet route tables
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[ec2.SubnetSelection(subnets=[private_isolated_subnet, private_with_internet_subnet])],
        )

        self.security_group = ec2.SecurityGroup(
            self,
            "SandboxSecurityGroup",
            vpc=self.vpc,
            description="Security group for Inspect EC2 sandbox instances",
            allow_all_outbound=True,
        )

        # SSM Interface Endpoints (required for Session Manager)

        self.vpc.add_interface_endpoint(
            "SSMEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SSM,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(subnets=[private_isolated_subnet]),
        )

        self.vpc.add_interface_endpoint(
            "SSMMessagesEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(subnets=[private_isolated_subnet]),
        )

        self.vpc.add_interface_endpoint(
            "EC2MessagesEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
            private_dns_enabled=True,
            subnets=ec2.SubnetSelection(subnets=[private_isolated_subnet]),
        )

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # IAM Role for EC2 instances
        self.instance_role = iam.Role(
            self,
            "SandboxInstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )

        # S3 permissions for file operations
        self.instance_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                ],
                resources=[
                    self.bucket.bucket_arn,
                    f"{self.bucket.bucket_arn}/*",
                ],
            )
        )

        # Create Instance Profile
        self.instance_profile = iam.InstanceProfile(
            self, "SandboxInstanceProfile", role=self.instance_role
        )

        # Outputs with exact environment variable names expected by settings class
        cdk.CfnOutput(
            self,
            "InspectEc2SandboxVpcId",
            value=self.vpc.vpc_id,
            description="VPC ID for EC2 sandbox instances",
            export_name="INSPECT-EC2-SANDBOX-VPC-ID",
        )

        cdk.CfnOutput(
            self,
            "InspectEc2SandboxSecurityGroupId",
            value=self.security_group.security_group_id,
            description="Security Group ID for EC2 sandbox instances",
            export_name="INSPECT-EC2-SANDBOX-SECURITY-GROUP-ID",
        )

        cdk.CfnOutput(
            self,
            "InspectEc2SandboxS3Bucket",
            value=self.bucket.bucket_name,
            description="S3 bucket for file operations",
            export_name="INSPECT-EC2-SANDBOX-S3-BUCKET",
        )

        cdk.CfnOutput(
            self,
            "InspectEc2SandboxInstanceProfile",
            value=self.instance_profile.instance_profile_name,
            description="Instance profile for EC2 sandbox instances",
            export_name="INSPECT-EC2-SANDBOX-INSTANCE-PROFILE",
        )

        cdk.CfnOutput(
            self,
            "InspectEc2SandboxSubnetIdNoInternet",
            value=private_isolated_subnet.subnet_id,
            description="Private isolated subnet ID for EC2 sandbox instances (no internet access)",
            export_name="INSPECT-EC2-SANDBOX-SUBNET-ID-NO-INTERNET",
        )

        cdk.CfnOutput(
            self,
            "InspectEc2SandboxSubnetIdWithInternet",
            value=private_with_internet_subnet.subnet_id,
            description="Private subnet ID for EC2 sandbox instances (with internet access)",
            export_name="INSPECT-EC2-SANDBOX-SUBNET-ID-WITH-INTERNET",
        )
