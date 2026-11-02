"""Create the S3 bucket and DynamoDB table for local runs (LocalStack, dynamodb-local or moto).

On real AWS these are owned by Terraform (src/infra/main.tf); keep both definitions in sync.
"""

import time

from botocore.exceptions import ClientError, EndpointConnectionError

from churn.shared.config import RISK_BAND_INDEX


def _retry_until_reachable(action, timeout_s: float = 60):
    # In Compose the emulators may still be booting when the seed container starts.
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return action()
        except EndpointConnectionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def ensure_bucket(s3, bucket: str, region: str) -> None:
    def create():
        try:
            s3.head_bucket(Bucket=bucket)
            return
        except ClientError:
            pass
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})

    _retry_until_reachable(create)


def ensure_table(dynamodb, table_name: str) -> None:
    client = dynamodb.meta.client

    def create():
        try:
            client.describe_table(TableName=table_name)
            return
        except client.exceptions.ResourceNotFoundException:
            pass
        client.create_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "CustomerID", "AttributeType": "S"},
                {"AttributeName": "risk_band", "AttributeType": "S"},
                {"AttributeName": "churn_probability", "AttributeType": "N"},
            ],
            KeySchema=[{"AttributeName": "CustomerID", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[{
                "IndexName": RISK_BAND_INDEX,
                "KeySchema": [
                    {"AttributeName": "risk_band", "KeyType": "HASH"},
                    {"AttributeName": "churn_probability", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        client.get_waiter("table_exists").wait(TableName=table_name)

    _retry_until_reachable(create)
