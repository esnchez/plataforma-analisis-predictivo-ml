import pytest
from moto import mock_aws

from churn.shared.aws import dynamodb_resource, s3_client
from churn.shared.config import get_settings


@pytest.fixture
def settings(monkeypatch):
    for var in ("AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL_DYNAMODB"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    return get_settings()


@pytest.fixture
def aws(settings):
    """Mocked S3 and DynamoDB. Resources are created by the seed job, as in Compose."""
    with mock_aws():
        yield s3_client(settings), dynamodb_resource(settings)
