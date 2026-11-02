from fastapi.testclient import TestClient

from churn.api import app as api_app
from churn.api.lookup import get_customer_score, list_customers_by_risk
from churn.localdev import backtest, seed
from churn.monitoring import job as monitoring_job
from churn.scoring import job as scoring_job
from churn.shared.features import load_snapshots
from churn.training import job as training_job


def test_services_end_to_end_from_raw_s3_to_api(aws, settings):
    s3, dynamodb = aws
    # Same order as Docker Compose: seed -> training -> scoring -> api.
    seed.run(settings, customers=400, months=18, seed=7)
    trained = training_job.run(settings)
    summary = scoring_job.run(settings)

    latest = load_snapshots(s3, settings.bucket, ["2026-10"])
    assert summary["snapshot_month"] == "2026-10"
    assert summary["model_version"] == trained["version"]
    assert summary["customers_scored"] == len(latest)
    assert trained["test_metrics"]["roc_auc"] > 0.7

    table = dynamodb.Table(settings.table)
    customer_id = latest["customer_id"].iloc[0]
    item = get_customer_score(table, customer_id)
    assert 0 <= item["churn_probability"] <= 1
    assert item["risk_band"] in {"high", "medium", "low"}
    assert item["model_version"] == trained["version"]

    ranked = list_customers_by_risk(table, "low", limit=10)
    probabilities = [c["churn_probability"] for c in ranked]
    assert probabilities == sorted(probabilities, reverse=True)

    # Scoring an older month again leaves only that month's customers: no stale items survive.
    summary = scoring_job.run(settings, "2026-09")
    assert table.scan(Select="COUNT")["Count"] == summary["customers_scored"]

    # Backtest then monitoring, as in Compose. With 18 months of data only July 2026 has both a known
    # outcome and 7 earlier months to train on; the months scored above are still waiting for outcomes.
    assert backtest.run(settings)["backtested_months"] == ["2026-07"]
    monitored = monitoring_job.run(settings)
    assert monitored["evaluated_months"] == ["2026-07"]
    assert monitored["pending_months"] == ["2026-09", "2026-10"]

    api_app._table.cache_clear()
    api_app._s3.cache_clear()
    client = TestClient(api_app.app)
    customer_id = table.scan(Limit=1)["Items"][0]["CustomerID"]
    assert client.get(f"/customers/{customer_id}/churn").status_code == 200
    assert client.get("/customers/C-99999/churn").status_code == 404
    assert client.get("/churn/at-risk", params={"band": "medium", "limit": 5}).status_code == 200

    summary = client.get("/churn/summary").json()
    assert summary["customers"] == table.scan(Select="COUNT")["Count"]
    performance = client.get("/model/performance").json()
    assert [m["snapshot_month"] for m in performance["months"]] == ["2026-07"]

    # Metrics are labeled by route template, not by customer ID.
    exposition = client.get("/metrics").text
    assert 'route="/customers/{customer_id}/churn",status="200"' in exposition
    assert 'churn_api_dynamodb_duration_seconds_count{operation="GetItem"}' in exposition
