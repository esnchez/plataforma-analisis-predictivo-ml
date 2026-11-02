"""Batch scoring: latest model + latest feature snapshot -> one DynamoDB item per active customer.

DynamoDB keeps only the current score per customer (overwritten every run) for fast lookups; the full
history of scores goes to S3 for analysis and monitoring. See docs/arquitectura.md.
"""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import xgboost as xgb

from churn.shared import model_store, predictions_store
from churn.shared.features import list_snapshot_months, load_snapshots
from churn.shared.metrics import assign_bands

TOP_FACTORS = 3


def score(booster: xgb.Booster, metadata: dict, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Probability, risk band and the features pushing each customer's risk up the most."""
    features = metadata["features"]
    matrix = xgb.DMatrix(snapshot[features], feature_names=features)
    probability = booster.predict(matrix)
    # SHAP contributions in log-odds; the last column is the bias term.
    contributions = booster.predict(matrix, pred_contribs=True)[:, :-1]

    top_factors = []
    for row, contrib in zip(snapshot[features].itertuples(index=False), contributions):
        ranked = [i for i in np.argsort(-contrib)[:TOP_FACTORS] if contrib[i] > 0]
        top_factors.append([{"feature": features[i], "value": float(row[i]), "impact": round(float(contrib[i]), 4)}
                            for i in ranked])

    return pd.DataFrame({
        "customer_id": snapshot["customer_id"].to_numpy(),
        "snapshot_month": snapshot["snapshot_month"].to_numpy(),
        "churn_probability": probability.round(4),
        "risk_band": assign_bands(probability, metadata["risk_bands"]),
        "top_factors": top_factors,
    })


def _to_dynamodb(value):
    # boto3 rejects Python floats for DynamoDB numbers; they have to be Decimal.
    if isinstance(value, float):
        return Decimal(str(round(value, 4)))
    if isinstance(value, list):
        return [_to_dynamodb(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamodb(v) for k, v in value.items()}
    return value


def write_predictions(table, scored: pd.DataFrame, metadata: dict, scored_at: str) -> None:
    with table.batch_writer(overwrite_by_pkeys=["CustomerID"]) as batch:
        for row in scored.itertuples(index=False):
            batch.put_item(Item=_to_dynamodb({
                "CustomerID": row.customer_id,
                "churn_probability": float(row.churn_probability),
                "risk_band": row.risk_band,
                "top_factors": row.top_factors,
                "snapshot_month": row.snapshot_month,
                "horizon_months": metadata["horizon_months"],
                "model_version": metadata["version"],
                "scored_at": scored_at,
            }))


def delete_stale(table, snapshot_month: str) -> int:
    """Remove customers not scored in this run (they cancelled), so lookups never return old scores."""
    stale = []
    kwargs = {"ProjectionExpression": "CustomerID, snapshot_month"}
    while True:
        page = table.scan(**kwargs)
        stale.extend(item["CustomerID"] for item in page["Items"] if item["snapshot_month"] != snapshot_month)
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    with table.batch_writer() as batch:
        for customer_id in stale:
            batch.delete_item(Key={"CustomerID": customer_id})
    return len(stale)


def run_scoring(s3, dynamodb, bucket: str, table_name: str, snapshot_month: str | None = None) -> dict:
    booster, metadata = model_store.load_latest(s3, bucket)
    snapshot_month = snapshot_month or list_snapshot_months(s3, bucket)[-1]
    snapshot = load_snapshots(s3, bucket, [snapshot_month])
    scored = score(booster, metadata, snapshot)
    scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    table = dynamodb.Table(table_name)
    write_predictions(table, scored, metadata, scored_at)
    removed = delete_stale(table, snapshot_month)

    predictions_store.write(s3, bucket, scored, metadata["version"], scored_at)

    return {
        "snapshot_month": snapshot_month,
        "model_version": metadata["version"],
        "customers_scored": int(len(scored)),
        "stale_items_removed": removed,
        "risk_bands": scored["risk_band"].value_counts().to_dict(),
    }
