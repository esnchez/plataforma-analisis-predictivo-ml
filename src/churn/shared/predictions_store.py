"""Prediction history in S3: predictions/snapshot_month=YYYY-MM/predictions.parquet.

Scoring writes the current month, the local backtest writes past months, monitoring reads them all.
"""

import json

import pandas as pd

from churn.shared.s3_io import list_keys, put_parquet, read_parquet

PREFIX = "predictions/snapshot_month="


def key(snapshot_month: str) -> str:
    return f"{PREFIX}{snapshot_month}/predictions.parquet"


def write(s3, bucket: str, scored: pd.DataFrame, model_version: str, scored_at: str) -> None:
    """Store one month of scores (columns of churn.scoring.score.score) with the model that made them."""
    months = scored["snapshot_month"].unique()
    if len(months) != 1:
        raise ValueError(f"Expected one snapshot month, got {list(months)}")
    history = scored.assign(top_factors=scored["top_factors"].apply(json.dumps),
                            model_version=model_version, scored_at=scored_at)
    put_parquet(s3, bucket, key(months[0]), history)


def list_months(s3, bucket: str) -> list[str]:
    return sorted({k[len(PREFIX):].split("/")[0] for k in list_keys(s3, bucket, PREFIX)})


def load(s3, bucket: str, snapshot_month: str) -> pd.DataFrame:
    return read_parquet(s3, bucket, key(snapshot_month))
