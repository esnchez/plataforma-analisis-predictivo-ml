import numpy as np
import pandas as pd

from churn.localdev.backtest import backtest_months, known_before
from churn.monitoring.monitor import evaluate_month


def _snapshots(months, label_known_through):
    rows = []
    for month in months:
        label = np.nan if month > label_known_through else 0.0
        rows += [{"customer_id": f"C-{i}", "snapshot_month": month, "label": label} for i in range(3)]
    return pd.DataFrame(rows)


def test_backtest_only_learns_from_outcomes_known_before_the_month():
    months = [str(m) for m in pd.period_range("2025-04", "2026-10", freq="M")]
    snapshots = _snapshots(months, label_known_through="2026-07")

    # Predicting January 2026 with a 3-month horizon: the October 2025 snapshot's window
    # (Oct-Dec) closed before January, the November one (Nov-Jan) did not.
    history = known_before(snapshots, "2026-01", horizon_months=3)
    assert history["snapshot_month"].max() == "2025-10"
    assert history["label"].notna().all()

    # Needs 7 months of history and a known outcome: January through July 2026.
    assert backtest_months(snapshots, horizon_months=3) == [f"2026-0{m}" for m in range(1, 8)]


def test_monitoring_evaluates_known_outcomes_and_waits_for_the_rest():
    predictions = pd.DataFrame({
        "customer_id": ["A", "B", "C", "D"], "snapshot_month": "2026-05", "model_version": "backtest-2026-05",
        "churn_probability": [0.9, 0.4, 0.2, 0.05], "risk_band": ["high", "high", "medium", "low"],
    })
    outcomes = pd.DataFrame({"customer_id": ["A", "B", "C", "D"], "label": [1.0, 0.0, 1.0, 0.0]})

    result = evaluate_month(predictions, outcomes)
    assert result["backtest"] is True
    assert result["roc_auc"] == 0.75
    assert result["high_band"] == {"flagged": 2, "precision": 0.5, "recall": 0.5}
    assert result["medium_or_high_band"] == {"flagged": 3, "precision": 0.6667, "recall": 1.0}

    assert evaluate_month(predictions, outcomes.assign(label=np.nan)) is None
