"""Train the XGBoost churn model on labeled snapshots.

Split is by time, never random: train on older snapshot months, stop early on the next ones and report
metrics on the most recent ones, which is how the model is used (trained on the past, scoring the
future). After evaluation the model is refit on every labeled month with the chosen number of trees.
See docs/modelo.md.
"""

import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from churn.shared.features import FEATURES
from churn.shared.metrics import assign_bands, evaluate

VALID_MONTHS = 3
TEST_MONTHS = 3
# Probability cut-offs for the risk bands stored in DynamoDB (probability >= cut-off).
RISK_BANDS = {"high": 0.30, "medium": 0.10}
XGB_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "aucpr",
    "learning_rate": 0.05,
    "max_depth": 4,
    "min_child_weight": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
}
MAX_TREES = 1000
EARLY_STOPPING_ROUNDS = 50


def time_split(labeled: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    months = sorted(labeled["snapshot_month"].unique())
    if len(months) < VALID_MONTHS + TEST_MONTHS + 1:
        raise ValueError(f"Need at least {VALID_MONTHS + TEST_MONTHS + 1} labeled snapshot months, got {len(months)}")
    test_months = months[-TEST_MONTHS:]
    valid_months = months[-(TEST_MONTHS + VALID_MONTHS):-TEST_MONTHS]
    train = labeled[~labeled["snapshot_month"].isin(test_months + valid_months)]
    valid = labeled[labeled["snapshot_month"].isin(valid_months)]
    test = labeled[labeled["snapshot_month"].isin(test_months)]
    return train, valid, test


def train(snapshots: pd.DataFrame) -> tuple[xgb.Booster, dict]:
    labeled = snapshots[snapshots["label"].notna()]
    train_df, valid_df, test_df = time_split(labeled)

    model = xgb.XGBClassifier(n_estimators=MAX_TREES, early_stopping_rounds=EARLY_STOPPING_ROUNDS, **XGB_PARAMS)
    model.fit(train_df[FEATURES], train_df["label"], eval_set=[(valid_df[FEATURES], valid_df["label"])],
              verbose=False)
    n_trees = model.best_iteration + 1
    test_p = model.predict_proba(test_df[FEATURES])[:, 1]
    test_metrics = evaluate(test_df["label"].to_numpy(), test_p, assign_bands(test_p, RISK_BANDS))

    # A linear baseline tells us whether XGBoost earns its extra complexity.
    baseline = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))
    baseline.fit(train_df[FEATURES], train_df["label"])
    baseline_p = baseline.predict_proba(test_df[FEATURES])[:, 1]

    # Final model: same settings, all labeled months, fixed number of trees found above.
    final = xgb.XGBClassifier(n_estimators=n_trees, **XGB_PARAMS)
    final.fit(labeled[FEATURES], labeled["label"], verbose=False)
    booster = final.get_booster()

    gain = booster.get_score(importance_type="gain")
    metadata = {
        "features": FEATURES,
        "risk_bands": RISK_BANDS,
        "xgboost_version": xgb.__version__,
        "params": {**XGB_PARAMS, "n_estimators": n_trees},
        "data": {
            "train_months": [train_df["snapshot_month"].min(), train_df["snapshot_month"].max()],
            "valid_months": [valid_df["snapshot_month"].min(), valid_df["snapshot_month"].max()],
            "test_months": [test_df["snapshot_month"].min(), test_df["snapshot_month"].max()],
            "final_fit_rows": int(len(labeled)),
        },
        "test_metrics": test_metrics,
        "baseline_logistic_regression": {
            "roc_auc": round(float(roc_auc_score(test_df["label"], baseline_p)), 4),
            "pr_auc": round(float(average_precision_score(test_df["label"], baseline_p)), 4),
        },
        "feature_importance_gain": {k: round(v, 3) for k, v in sorted(gain.items(), key=lambda kv: -kv[1])},
    }
    return booster, metadata
