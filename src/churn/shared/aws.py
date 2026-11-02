"""boto3 clients used by every service. Depends on boto3 only, so the API image stays small."""

import os

import boto3
from botocore.config import Config

from churn.shared.config import Settings

# boto3 defaults to 10 pooled connections, fewer than FastAPI's 40 worker threads: under load,
# requests would queue for a connection. Standard retry mode backs off on throttling.
BOTO_CONFIG = Config(max_pool_connections=50, retries={"mode": "standard"})


def s3_client(settings: Settings):
    config = BOTO_CONFIG
    if os.environ.get("AWS_ENDPOINT_URL_S3") or os.environ.get("AWS_ENDPOINT_URL"):
        # Emulators are reached by host name (localhost, localstack), so the bucket has to go in the
        # path; the default virtual-host style would look up "<bucket>.localstack" in DNS.
        config = config.merge(Config(s3={"addressing_style": "path"}))
    return boto3.client("s3", region_name=settings.region, config=config)


def dynamodb_resource(settings: Settings):
    return boto3.resource("dynamodb", region_name=settings.region, config=BOTO_CONFIG)
