"""Monitoring job: rebuild features (they carry the real outcomes), evaluate every past month of
predictions whose outcome is known, and write the report to S3 for the API and the frontend.

Runs monthly after scoring and exits (scheduled task on AWS). Reads S3, writes only
monitoring/model_performance.json.
    python -m churn.monitoring
"""

import json
from datetime import datetime, timezone

from churn.monitoring.monitor import build_report
from churn.shared import features
from churn.shared.aws import s3_client
from churn.shared.config import PERFORMANCE_KEY, Settings, get_settings
from churn.shared.s3_io import put_json


def run(settings: Settings) -> dict:
    s3 = s3_client(settings)
    snapshots = features.run_feature_build(s3, settings.bucket, settings.horizon_months)
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_months": settings.horizon_months,
        **build_report(s3, settings.bucket, snapshots),
    }
    put_json(s3, settings.bucket, PERFORMANCE_KEY, report)
    return {
        "evaluated_months": [m["snapshot_month"] for m in report["months"]],
        "pending_months": report["pending_months"],
        "high_band_precision": {m["snapshot_month"]: m["high_band"]["precision"] for m in report["months"]},
    }


def main() -> None:
    print(json.dumps(run(get_settings()), indent=2))
