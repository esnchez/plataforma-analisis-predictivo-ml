"""Training job: rebuild features from raw/, train XGBoost, publish a new model version to S3.

Runs once and exits (ECS Fargate scheduled task on AWS). Writes S3 only, never DynamoDB.
    python -m churn.training
"""

import json

from churn.shared import features, model_store
from churn.shared.aws import s3_client
from churn.shared.config import Settings, get_settings
from churn.training.train import train


def run(settings: Settings) -> dict:
    s3 = s3_client(settings)
    features.run_feature_build(s3, settings.bucket, settings.horizon_months)
    booster, metadata = train(features.load_snapshots(s3, settings.bucket))
    version = model_store.publish(s3, settings.bucket, booster, metadata, settings.horizon_months)
    return {"version": version, "test_metrics": metadata["test_metrics"],
            "baseline_logistic_regression": metadata["baseline_logistic_regression"]}


def main() -> None:
    print(json.dumps(run(get_settings()), indent=2))
