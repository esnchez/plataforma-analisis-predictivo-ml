"""Feature engineering: raw S3 data -> one row per (customer, snapshot month).

A snapshot month T means "the first day of month T". Its features only use data from months < T and
its label says whether the customer cancels in months T .. T+horizon-1. This strict time split is what
keeps the model honest: nothing a customer does on or after the snapshot date can leak into its
features. tests/test_features.py checks it. See docs/features-y-etiquetas.md.
"""

import numpy as np
import pandas as pd

from churn.shared.raw_data import load_latest_partition, load_source
from churn.shared.s3_io import list_keys, put_parquet, read_parquet

PLAN_TIER = {"basic": 0, "pro": 1, "enterprise": 2}
# Snapshots need this many months of history in the data window to have complete rolling features.
WARMUP_MONTHS = 6

FEATURES = [
    "tenure_months",
    "plan_tier",
    "is_annual",
    "seats",
    "months_to_renewal",
    "logins_1m",
    "logins_3m_avg",
    "logins_trend",
    "active_seat_ratio_1m",
    "active_seat_ratio_3m_avg",
    "feature_events_3m",
    "tickets_3m",
    "high_sev_tickets_3m",
    "payment_failures_3m",
]


def _to_month(timestamps: pd.Series) -> pd.Series:
    # Source timestamps are UTC ("...Z"); months are computed in UTC.
    return pd.to_datetime(timestamps, utc=True).dt.tz_convert(None).dt.to_period("M")


def build_snapshots(crm: pd.DataFrame, usage: pd.DataFrame, tickets: pd.DataFrame,
                    billing: pd.DataFrame, horizon_months: int) -> pd.DataFrame:
    """Return features (+ label where known) for every customer active at every snapshot month."""
    usage = usage.assign(month=pd.PeriodIndex(usage["month"], freq="M"))
    first_month, last_month = usage["month"].min(), usage["month"].max()

    customers = crm[["customer_id", "plan", "seats", "contract"]].copy()
    customers["signup_month"] = _to_month(crm["signup_date"])
    cancels = billing[billing["event_type"] == "subscription_cancelled"].assign(
        cancel_month=lambda d: _to_month(d["occurred_at"]))
    customers = customers.merge(cancels.groupby("customer_id", as_index=False)["cancel_month"].min(),
                                on="customer_id", how="left")

    # Customer x month grid over the months each customer was subscribed. Months without activity
    # become zeros instead of missing rows, so rolling windows count calendar months.
    all_months = pd.DataFrame({"month": pd.period_range(first_month, last_month, freq="M")})
    grid = customers.merge(all_months, how="cross")
    grid = grid[(grid["month"] >= grid["signup_month"])
                & (grid["cancel_month"].isna() | (grid["month"] <= grid["cancel_month"]))]

    monthly_usage = usage.groupby(["customer_id", "month"], as_index=False)[
        ["active_seats", "logins", "feature_events"]].sum()
    tickets = tickets.assign(month=_to_month(tickets["created_at"]),
                             high=lambda d: (d["severity"] == "high").astype(int))
    monthly_tickets = tickets.groupby(["customer_id", "month"], as_index=False).agg(
        tickets=("ticket_id", "count"), high_sev_tickets=("high", "sum"))
    failures = billing[billing["event_type"] == "payment_failed"].assign(
        month=lambda d: _to_month(d["occurred_at"]))
    monthly_failures = failures.groupby(["customer_id", "month"], as_index=False).agg(
        payment_failures=("event_id", "count"))

    for frame in (monthly_usage, monthly_tickets, monthly_failures):
        grid = grid.merge(frame, on=["customer_id", "month"], how="left")
    activity_cols = ["active_seats", "logins", "feature_events", "tickets", "high_sev_tickets",
                     "payment_failures"]
    grid[activity_cols] = grid[activity_cols].fillna(0)
    grid = grid.sort_values(["customer_id", "month"]).reset_index(drop=True)

    by_customer = grid.groupby("customer_id")

    def rolling(col: str, window: int, how: str) -> pd.Series:
        r = by_customer[col].rolling(window, min_periods=1)
        return getattr(r, how)().reset_index(level=0, drop=True)

    grid["active_seat_ratio"] = grid["active_seats"] / grid["seats"].clip(lower=1)
    out = pd.DataFrame({"customer_id": grid["customer_id"]})
    # Row for month m describes the customer as of the start of month m+1, the snapshot month.
    out["snapshot_month"] = grid["month"] + 1
    out["tenure_months"] = (out["snapshot_month"] - grid["signup_month"]).apply(lambda d: d.n)
    out["plan_tier"] = grid["plan"].map(PLAN_TIER)
    out["is_annual"] = (grid["contract"] == "annual").astype(int)
    out["seats"] = grid["seats"]
    out["months_to_renewal"] = np.where(out["is_annual"] == 1, 12 - out["tenure_months"] % 12, 1)
    out["logins_1m"] = grid["logins"]
    out["logins_3m_avg"] = rolling("logins", 3, "mean")
    previous_3m_avg = out.groupby("customer_id")["logins_3m_avg"].shift(3)
    out["logins_trend"] = ((out["logins_3m_avg"] + 1) / (previous_3m_avg + 1)).fillna(1.0)
    out["active_seat_ratio_1m"] = grid["active_seat_ratio"]
    out["active_seat_ratio_3m_avg"] = rolling("active_seat_ratio", 3, "mean")
    out["feature_events_3m"] = rolling("feature_events", 3, "sum")
    out["tickets_3m"] = rolling("tickets", 3, "sum")
    out["high_sev_tickets_3m"] = rolling("high_sev_tickets", 3, "sum")
    out["payment_failures_3m"] = rolling("payment_failures", 3, "sum")

    # Keep customers still subscribed at the snapshot date (cancelling in month m means they are
    # gone by m+1) and drop early snapshots whose rolling windows would be cut by the data window.
    cancel_month = grid["cancel_month"]
    out = out[(cancel_month.isna() | (cancel_month >= out["snapshot_month"]))
              & (out["snapshot_month"] >= first_month + WARMUP_MONTHS)].copy()

    # Label: cancels within [T, T + horizon - 1]. Unknown (NaN) when that window is not fully observed.
    cancel_month = cancel_month.loc[out.index]
    window_end = out["snapshot_month"] + (horizon_months - 1)
    churned = cancel_month.notna() & (cancel_month <= window_end)
    out["label"] = np.where(window_end <= last_month, churned.astype(float), np.nan)

    out["snapshot_month"] = out["snapshot_month"].astype(str)
    return out.reset_index(drop=True)


def feature_key(snapshot_month: str) -> str:
    return f"features/snapshot_month={snapshot_month}/features.parquet"


def run_feature_build(s3, bucket: str, horizon_months: int) -> pd.DataFrame:
    """Rebuild every snapshot partition from raw data in S3. Returns the full snapshot table."""
    snapshots = build_snapshots(
        crm=load_latest_partition(s3, bucket, "crm_customers"),
        usage=load_source(s3, bucket, "usage"),
        tickets=load_source(s3, bucket, "support_tickets"),
        billing=load_source(s3, bucket, "billing_events"),
        horizon_months=horizon_months,
    )
    for month, part in snapshots.groupby("snapshot_month"):
        put_parquet(s3, bucket, feature_key(month), part)
    return snapshots


def list_snapshot_months(s3, bucket: str) -> list[str]:
    keys = list_keys(s3, bucket, "features/snapshot_month=")
    return sorted({key.split("/")[1].split("=")[1] for key in keys})


def load_snapshots(s3, bucket: str, months: list[str] | None = None) -> pd.DataFrame:
    months = months or list_snapshot_months(s3, bucket)
    return pd.concat([read_parquet(s3, bucket, feature_key(m)) for m in months], ignore_index=True)
