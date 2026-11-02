"""Synthetic SaaS customer data, shaped like what real source systems would export.

Each customer has a hidden "health" score that drifts month to month (with occasional shocks such as
losing an internal champion). Health drives everything observable (seat usage, logins, support tickets,
failed payments) and the probability of cancelling. The model never sees health: it has to infer it
from the observable signals, which is the same problem it would face with real data.
See docs/datos-sinteticos.md for the full generative story and its known simplifications.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

PLANS = ("basic", "pro", "enterprise")
PLAN_PROBS = (0.50, 0.35, 0.15)
PLAN_SEATS = {"basic": (1, 6), "pro": (5, 31), "enterprise": (30, 301)}  # [low, high) seat ranges
PLAN_PRICE_PER_SEAT = {"basic": 15, "pro": 40, "enterprise": 65}  # USD per seat per month
PLAN_ANNUAL_PROB = {"basic": 0.2, "pro": 0.5, "enterprise": 0.9}
PLAN_HEALTH = {"basic": -0.2, "pro": 0.2, "enterprise": 0.5}
INDUSTRIES = ("software", "retail", "finance", "healthcare", "education", "manufacturing")
COUNTRIES = ("US", "GB", "DE", "ES", "FR", "MX", "BR")


@dataclass
class RawBatch:
    source: str
    partition: str
    records: list[dict]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _random_ts(rng, month: pd.Period) -> str:
    day = int(rng.integers(1, month.days_in_month + 1))
    second = int(rng.integers(0, 86400))
    ts = pd.Timestamp(year=month.year, month=month.month, day=day) + pd.Timedelta(seconds=second)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate(n_customers: int = 2000, n_months: int = 24, last_month: str = "2026-09",
             seed: int = 42) -> list[RawBatch]:
    """Simulate n_months of activity ending at last_month (inclusive) and return it as raw batches."""
    rng = np.random.default_rng(seed)
    months = pd.period_range(end=pd.Period(last_month, freq="M"), periods=n_months, freq="M")
    n = n_customers

    plan = rng.choice(PLANS, size=n, p=PLAN_PROBS)
    seats = np.array([rng.integers(*PLAN_SEATS[p]) for p in plan])
    annual = np.array([rng.random() < PLAN_ANNUAL_PROB[p] for p in plan])
    industry = rng.choice(INDUSTRIES, size=n)
    country = rng.choice(COUNTRIES, size=n)

    # 40% of customers already exist when the data window starts; the rest sign up during it.
    # signup_idx is the signup month relative to months[0] (negative = before the window).
    existing = rng.random(n) < 0.4
    signup_idx = np.where(existing, -rng.integers(1, 37, size=n), rng.integers(0, n_months, size=n))

    health = rng.normal([PLAN_HEALTH[p] for p in plan], 0.8)
    cancelled = np.zeros(n, dtype=bool)
    last_payment_failure = np.full(n, -99)

    usage, tickets, billing = {}, {}, {}
    ticket_seq = billing_seq = 0

    for t, month in enumerate(months):
        usage[month], tickets[month], billing[month] = [], [], []
        shock = rng.random(n) < 0.03
        health = health + rng.normal(-0.01, 0.2, size=n) - shock * 1.2

        tenure = t - signup_idx
        active = (tenure >= 0) & ~cancelled
        utilisation = _sigmoid(health + 0.8)
        active_seats = rng.binomial(seats, np.clip(utilisation * 0.95, 0, 1))
        logins = rng.poisson(active_seats * 12 * _sigmoid(health + 0.5))
        feature_events = rng.poisson(logins * 0.6 * _sigmoid(health))
        n_tickets = rng.poisson((0.2 + 0.6 * _sigmoid(-health)) * (1 + np.log1p(seats) / 3))
        high_sev_prob = _sigmoid(-health - 1.5)

        # Billing: monthly contracts are invoiced every month, annual ones on each contract anniversary.
        invoiced = active & np.where(annual, tenure % 12 == 0, True)
        payment_failed = invoiced & (rng.random(n) < 0.01 + 0.10 * _sigmoid(-health - 1))
        last_payment_failure = np.where(payment_failed, t, last_payment_failure)

        high_tickets_now = np.zeros(n)
        for i in np.flatnonzero(active):
            cid = f"C-{i + 1:05d}"
            usage[month].append({
                "customer_id": cid, "month": str(month), "active_seats": int(active_seats[i]),
                "logins": int(logins[i]), "feature_events": int(feature_events[i]),
            })
            for _ in range(n_tickets[i]):
                ticket_seq += 1
                severity = "high" if rng.random() < high_sev_prob[i] else str(rng.choice(["low", "medium"]))
                high_tickets_now[i] += severity == "high"
                tickets[month].append({
                    "ticket_id": f"T-{ticket_seq:07d}", "customer_id": cid,
                    "created_at": _random_ts(rng, month), "severity": severity,
                    "channel": str(rng.choice(["email", "chat", "phone"])),
                })
            if invoiced[i]:
                amount = int(seats[i] * PLAN_PRICE_PER_SEAT[plan[i]] * (12 if annual[i] else 1))
                if payment_failed[i]:
                    billing_seq += 1
                    billing[month].append({"event_id": f"B-{billing_seq:07d}", "customer_id": cid,
                                           "event_type": "payment_failed", "occurred_at": _random_ts(rng, month),
                                           "amount_usd": amount})
                if not payment_failed[i] or rng.random() < 0.7:  # most failed payments are recovered
                    billing_seq += 1
                    billing[month].append({"event_id": f"B-{billing_seq:07d}", "customer_id": cid,
                                           "event_type": "invoice_paid", "occurred_at": _random_ts(rng, month),
                                           "amount_usd": amount})

        # Churn: monthly contracts can cancel any month, annual ones only in the last month of a
        # contract year (so their hazard is concentrated there and therefore higher).
        recent_failure = (t - last_payment_failure) <= 2
        z = -3.6 - 1.5 * health + 1.2 * recent_failure + 0.4 * high_tickets_now + 0.5 * (tenure < 6)
        eligible = active & (tenure >= 1) & np.where(annual, tenure % 12 == 11, True)
        z = np.where(annual, z + 2.2, z)
        churn_now = eligible & (rng.random(n) < _sigmoid(z))
        for i in np.flatnonzero(churn_now):
            billing_seq += 1
            billing[month].append({"event_id": f"B-{billing_seq:07d}", "customer_id": f"C-{i + 1:05d}",
                                   "event_type": "subscription_cancelled",
                                   "occurred_at": _random_ts(rng, month), "amount_usd": 0})
        cancelled |= churn_now

    # The CRM exports a full customer table (including cancelled customers) at the end of the window.
    crm = []
    for i in range(n):
        signup_month = months[0] + int(signup_idx[i])
        crm.append({
            "customer_id": f"C-{i + 1:05d}", "signup_date": _random_ts(rng, signup_month)[:10],
            "plan": str(plan[i]), "seats": int(seats[i]), "contract": "annual" if annual[i] else "monthly",
            "industry": str(industry[i]), "country": str(country[i]),
        })

    export_date = months[-1].end_time.strftime("%Y-%m-%d")
    batches = [RawBatch("crm_customers", f"export_date={export_date}", crm)]
    for month in months:
        batches.append(RawBatch("usage", f"month={month}", usage[month]))
        batches.append(RawBatch("support_tickets", f"month={month}", tickets[month]))
        batches.append(RawBatch("billing_events", f"month={month}", billing[month]))
    return batches
