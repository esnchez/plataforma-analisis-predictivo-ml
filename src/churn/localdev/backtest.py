"""Backtest (local only): fill predictions/ for past months as if the system had been running back then.

For each past month M it trains a model only on snapshots whose outcome was already known before M
(label window ended by M-1), scores the customers active at M with it, and stores the scores the same
way the scoring service does, with model_version "backtest-M". The monitoring service then evaluates
them against what really happened. It never touches DynamoDB or models/.

This is a tool, not a service, so it reuses the training and scoring code directly.
    python -m churn.localdev.backtest [--months 2026-01 2026-02 ...]
"""

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from churn.scoring.score import score
from churn.shared import features, predictions_store
from churn.shared.aws import s3_client
from churn.shared.config import Settings, get_settings
from churn.training.train import TEST_MONTHS, VALID_MONTHS, train

# train() needs this many labeled months: train + validation + test.
MIN_TRAINING_MONTHS = VALID_MONTHS + TEST_MONTHS + 1


def known_before(snapshots: pd.DataFrame, month: str, horizon_months: int) -> pd.DataFrame:
    """Labeled snapshots whose whole label window ended before `month`: all a model could learn from then."""
    last_known = str(pd.Period(month, freq="M") - horizon_months)
    return snapshots[(snapshots["snapshot_month"] <= last_known) & snapshots["label"].notna()]


def backtest_months(snapshots: pd.DataFrame, horizon_months: int) -> list[str]:
    """Past months with a known outcome and enough earlier history to train a model."""
    labeled_months = sorted(snapshots.loc[snapshots["label"].notna(), "snapshot_month"].unique())
    return [m for m in labeled_months
            if known_before(snapshots, m, horizon_months)["snapshot_month"].nunique() >= MIN_TRAINING_MONTHS]


def run(settings: Settings, months: list[str] | None = None) -> dict:
    s3 = s3_client(settings)
    snapshots = features.run_feature_build(s3, settings.bucket, settings.horizon_months)
    months = months or backtest_months(snapshots, settings.horizon_months)
    scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trained_through = {}
    for month in months:
        history = known_before(snapshots, month, settings.horizon_months)
        booster, metadata = train(history)
        scored = score(booster, metadata, snapshots[snapshots["snapshot_month"] == month])
        predictions_store.write(s3, settings.bucket, scored, f"backtest-{month}", scored_at)
        trained_through[month] = history["snapshot_month"].max()
    return {"backtested_months": months, "training_data_through": trained_through}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m churn.localdev.backtest")
    parser.add_argument("--months", nargs="+", help="YYYY-MM months to backtest (default: all possible)")
    args = parser.parse_args(argv)
    print(json.dumps(run(get_settings(), args.months), indent=2))


if __name__ == "__main__":
    main()
