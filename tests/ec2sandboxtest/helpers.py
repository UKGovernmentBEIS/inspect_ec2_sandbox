import boto3
from botocore.exceptions import BotoCoreError, ClientError


def has_aws_creds():
    try:
        boto3.client("sts").get_caller_identity()
        return True
    except (BotoCoreError, ClientError):
        return False



