import pandas as pd

from churn.localdev import synthetic
from churn.shared.features import FEATURES, build_snapshots


def _frames(batches):
    frames = {}
    for batch in batches:
        frames.setdefault(batch.source, []).extend(batch.records)
    return {source: pd.DataFrame(records) for source, records in frames.items()}


def _build(frames, horizon=3):
    return build_snapshots(frames["crm_customers"], frames["usage"], frames["support_tickets"],
                           frames["billing_events"], horizon)


def test_features_ignore_data_from_the_snapshot_month_onwards():
    frames = _frames(synthetic.generate(n_customers=200, n_months=18, seed=1))
    before = _build(frames)
    snapshot = "2026-06"

    # Make June 2026 activity absurd: features for the June snapshot must not change.
    usage = frames["usage"].copy()
    usage.loc[usage["month"] == snapshot, ["logins", "active_seats", "feature_events"]] = 10_000
    after = _build({**frames, "usage": usage})

    cols = ["customer_id", *FEATURES]
    pd.testing.assert_frame_equal(
        before[before["snapshot_month"] == snapshot][cols].reset_index(drop=True),
        after[after["snapshot_month"] == snapshot][cols].reset_index(drop=True),
    )


def test_labels_follow_the_cancellation_window():
    crm = pd.DataFrame([
        {"customer_id": "A", "signup_date": "2025-01-10", "plan": "pro", "seats": 10, "contract": "monthly"},
        {"customer_id": "B", "signup_date": "2025-01-10", "plan": "pro", "seats": 10, "contract": "monthly"},
    ])
    months = [str(m) for m in pd.period_range("2025-01", "2025-12", freq="M")]
    usage = pd.DataFrame([{"customer_id": c, "month": m, "active_seats": 5, "logins": 50, "feature_events": 20}
                          for c in ("A", "B") for m in months])
    tickets = pd.DataFrame(columns=["ticket_id", "customer_id", "created_at", "severity"])
    billing = pd.DataFrame([{"event_id": "B1", "customer_id": "A", "event_type": "subscription_cancelled",
                             "occurred_at": "2025-09-15T10:00:00Z", "amount_usd": 0}])

    snaps = build_snapshots(crm, usage, tickets, billing, horizon_months=3).set_index(["customer_id", "snapshot_month"])

    # First snapshot is July (6 warm-up months). A cancels in September: positive for July, August and
    # September (each window reaches September), gone from October on.
    assert ("A", "2025-06") not in snaps.index
    assert [snaps.loc[("A", m), "label"] for m in ("2025-07", "2025-08", "2025-09")] == [1, 1, 1]
    assert ("A", "2025-10") not in snaps.index
    # B never cancels; snapshots whose 3-month window goes past December are unlabeled.
    assert snaps.loc[("B", "2025-10"), "label"] == 0
    assert pd.isna(snaps.loc[("B", "2025-11"), "label"])
    assert pd.isna(snaps.loc[("B", "2026-01"), "label"])
