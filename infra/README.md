# Sample Infrastructure

This folder contains a CDK project that allows you to deploy a basic VPC for 
use by Inspect EC2 sandboxes.

## Architecture

It creates a VPC with three subnets, all in a single AZ.

One of the subnets is completely isolated from the internet, the other is private with outgoing internet access.

It creates an S3 bucket which is needed for data communication with sandbox EC2 instances.

## Deploying

Run `cdk deploy`.

You will get output similar to the following

```

Outputs:
Ec2SandboxStack.InspectEc2SandboxInstanceProfile = Ec2SandboxStack-SandboxInstanceProfile80B4A45B-IGNnr7EW7XKa
Ec2SandboxStack.InspectEc2SandboxS3Bucket = ec2sandboxstack-databuckete3889a50-ydsv8qonqacs
Ec2SandboxStack.InspectEc2SandboxSecurityGroupId = sg-03c3b0f3019b6dfb0
Ec2SandboxStack.InspectEc2SandboxSubnetIdNoInternet = subnet-0cd3ba954a5a9c706
Ec2SandboxStack.InspectEc2SandboxSubnetIdWithInternet = subnet-0d50dede5b04b7a3e
Ec2SandboxStack.InspectEc2SandboxVpcId = vpc-0e0e5631a687f45e7
Stack ARN:
arn:aws:cloudformation:eu-west-2:537124965764:stack/Ec2SandboxStack/f391d090-7da9-11f0-acd8-0270a713a8c5

```

To obtain the environment variables needed by the sandbox provider at runtime, 
you can use the following convenience script:

```bash
export ARN=[arn]
aws cloudformation describe-stacks --stack-name "$ARN" --query 'Stacks[0].Outputs[].{Key:ExportName,Value:OutputValue}' --output text | awk '{gsub(/-/, "_", $1); print "export " $1 "=" $2}'
```

You will see output like this

```bash
export INSPECT_EC2_SANDBOX_S3_BUCKET=ec2sandboxstack-databuckete3889a50-ydsv8qonqacs
export INSPECT_EC2_SANDBOX_SUBNET_ID_WITH_INTERNET=subnet-0d50dede5b04b7a3e
export INSPECT_EC2_SANDBOX_VPC_ID=vpc-0e0e5631a687f45e7
export INSPECT_EC2_SANDBOX_SUBNET_ID_NO_INTERNET=subnet-0cd3ba954a5a9c706
export INSPECT_EC2_SANDBOX_SECURITY_GROUP_ID=sg-03c3b0f3019b6dfb0
export INSPECT_EC2_SANDBOX_INSTANCE_PROFILE=Ec2SandboxStack-SandboxInstanceProfile80B4A45B-IGNnr7EW7XKa
```

Note, for an individual eval, you need `INSPECT_EC2_SANDBOX_SUBNET_ID` 
(without the `(WITH|NO)_INTERNET` suffix). 

Choose either

`export INSPECT_EC2_SANDBOX_SUBNET_ID=$INSPECT_EC2_SANDBOX_SUBNET_ID_WITH_INTERNET`

or

`export INSPECT_EC2_SANDBOX_SUBNET_ID=$INSPECT_EC2_SANDBOX_SUBNET_ID_NO_INTERNET`

according to your use case.