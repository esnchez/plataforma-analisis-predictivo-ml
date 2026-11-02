"""Model versions in S3: training publishes them, scoring loads the latest one.

Layout: models/churn/<version>/model.json + metadata.json, and models/churn/latest.json pointing at the
version scoring should use. See docs/arquitectura.md.
"""

from datetime import datetime, timezone

import xgboost as xgb

from churn.shared.config import MODEL_PREFIX
from churn.shared.s3_io import put_json, read_json


def publish(s3, bucket: str, booster: xgb.Booster, metadata: dict, horizon_months: int) -> str:
    """Write model + metadata under a new version and point latest.json at it."""
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metadata = {**metadata, "version": version, "horizon_months": horizon_months}
    s3.put_object(Bucket=bucket, Key=f"{MODEL_PREFIX}/{version}/model.json", Body=bytes(booster.save_raw("json")))
    put_json(s3, bucket, f"{MODEL_PREFIX}/{version}/metadata.json", metadata)
    # The pointer is written last so a half-published version is never picked up by scoring.
    put_json(s3, bucket, f"{MODEL_PREFIX}/latest.json", {"version": version})
    return version


def load_latest(s3, bucket: str) -> tuple[xgb.Booster, dict]:
    version = read_json(s3, bucket, f"{MODEL_PREFIX}/latest.json")["version"]
    booster = xgb.Booster()
    booster.load_model(bytearray(s3.get_object(Bucket=bucket, Key=f"{MODEL_PREFIX}/{version}/model.json")["Body"].read()))
    return booster, read_json(s3, bucket, f"{MODEL_PREFIX}/{version}/metadata.json")
