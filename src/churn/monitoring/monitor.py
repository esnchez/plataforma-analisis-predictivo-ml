"""Compare past predictions with what actually happened.

A prediction for snapshot month M can be checked once its label window has closed (M + horizon - 1 is
in the data). The real outcome is the label of the customer's feature snapshot for M, rebuilt from the
billing events in raw/. Metrics are the same as training's test metrics (churn.shared.metrics), so the
two can be compared directly. See docs/modelo.md.
"""

import pandas as pd

from churn.shared import predictions_store
from churn.shared.metrics import evaluate


def evaluate_month(predictions: pd.DataFrame, outcomes: pd.DataFrame) -> dict | None:
    """Metrics for one month of predictions, or None while its outcome is still unknown."""
    joined = predictions.merge(outcomes[["customer_id", "label"]], on="customer_id", how="inner")
    if joined.empty or joined["label"].isna().any():
        return None
    version = str(predictions["model_version"].iloc[0])
    return {
        "snapshot_month": str(predictions["snapshot_month"].iloc[0]),
        "model_version": version,
        "backtest": version.startswith("backtest-"),
        "customers_predicted": int(len(predictions)),
        **evaluate(joined["label"].to_numpy(), joined["churn_probability"].to_numpy(),
                   joined["risk_band"].to_numpy()),
    }


def build_report(s3, bucket: str, snapshots: pd.DataFrame) -> dict:
    """Evaluate every month in predictions/ against the outcomes in `snapshots` (features + labels)."""
    evaluated, pending = [], []
    for month in predictions_store.list_months(s3, bucket):
        result = evaluate_month(predictions_store.load(s3, bucket, month),
                                snapshots[snapshots["snapshot_month"] == month])
        if result is None:
            pending.append(month)
        else:
            evaluated.append(result)
    return {"months": evaluated, "pending_months": pending}
