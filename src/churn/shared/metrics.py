"""Risk bands and evaluation metrics, shared by training (test months) and monitoring (real outcomes)."""

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score


def assign_bands(probability: np.ndarray, cutoffs: dict) -> np.ndarray:
    """Band per probability: the highest band whose cut-off it reaches, otherwise "low"."""
    bands = np.full(len(probability), "low", dtype=object)
    for band, cutoff in sorted(cutoffs.items(), key=lambda kv: kv[1]):
        bands[probability >= cutoff] = band
    return bands


def _flag_metrics(y: np.ndarray, flagged: np.ndarray) -> dict:
    return {
        "flagged": int(flagged.sum()),
        "precision": round(float(y[flagged].mean()), 4) if flagged.any() else None,
        "recall": round(float(y[flagged].sum() / y.sum()), 4) if y.sum() else None,
    }


def evaluate(y: np.ndarray, p: np.ndarray, bands: np.ndarray) -> dict:
    """Ranking, calibration and business-facing metrics for one set of predictions with known outcomes."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    bands = np.asarray(bands)
    top_decile = np.argsort(-p)[: max(1, len(p) // 10)]
    base_rate = float(y.mean())
    both_classes = 0 < y.sum() < len(y)
    return {
        "rows": int(len(y)),
        "positives": int(y.sum()),
        "base_rate": round(base_rate, 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4) if both_classes else None,
        "pr_auc": round(float(average_precision_score(y, p)), 4) if both_classes else None,
        "brier": round(float(brier_score_loss(y, p)), 4),
        "top_decile_precision": round(float(y[top_decile].mean()), 4),
        "top_decile_lift": round(float(y[top_decile].mean() / base_rate), 2) if base_rate else None,
        # What customer success sees: the customers in a band, and how many of them really churned.
        "high_band": _flag_metrics(y, bands == "high"),
        "medium_or_high_band": _flag_metrics(y, np.isin(bands, ["high", "medium"])),
    }
