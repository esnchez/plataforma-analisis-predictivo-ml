"""Scoring job: rebuild features from raw/, score the latest snapshot with the latest model.

Runs once a month and exits (Lambda container image on an EventBridge schedule on AWS). It is the only
service that writes to DynamoDB. Needs a published model, so training must have run at least once.
    python -m churn.scoring [--snapshot-month YYYY-MM]
"""

import argparse
import json

from churn.scoring.score import run_scoring
from churn.shared import features
from churn.shared.aws import dynamodb_resource, s3_client
from churn.shared.config import Settings, get_settings


def run(settings: Settings, snapshot_month: str | None = None) -> dict:
    s3 = s3_client(settings)
    features.run_feature_build(s3, settings.bucket, settings.horizon_months)
    return run_scoring(s3, dynamodb_resource(settings), settings.bucket, settings.table, snapshot_month)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m churn.scoring")
    parser.add_argument("--snapshot-month", help="YYYY-MM, defaults to the latest")
    args = parser.parse_args(argv)
    print(json.dumps(run(get_settings(), args.snapshot_month), indent=2))
