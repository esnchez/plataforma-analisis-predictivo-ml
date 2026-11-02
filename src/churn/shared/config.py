"""Settings and resource names shared by every service.

AWS endpoints are not configured here: boto3 reads them from the environment by itself.
AWS_ENDPOINT_URL applies to every service, and AWS_ENDPOINT_URL_S3 / AWS_ENDPOINT_URL_DYNAMODB override
it per service (S3 on LocalStack, DynamoDB on dynamodb-local). On real AWS all of them are unset.
"""

import os
from dataclasses import dataclass

# S3 prefix for model versions: training writes it, scoring reads it.
MODEL_PREFIX = "models/churn"
# DynamoDB index listing customers of one risk band by probability: scoring fills it, the API queries it.
RISK_BAND_INDEX = "RiskBandIndex"
# Model performance report: monitoring writes it, the API serves it.
PERFORMANCE_KEY = "monitoring/model_performance.json"
RISK_BAND_NAMES = ("high", "medium", "low")


@dataclass(frozen=True)
class Settings:
    region: str
    bucket: str
    table: str
    # Months ahead the model predicts: label = "cancels within the next N months".
    horizon_months: int


def get_settings() -> Settings:
    return Settings(
        region=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2",
        bucket=os.environ.get("CHURN_BUCKET", "saas-churn-data"),
        table=os.environ.get("CHURN_TABLE", "ChurnPredictions"),
        horizon_months=int(os.environ.get("CHURN_HORIZON_MONTHS", "3")),
    )
