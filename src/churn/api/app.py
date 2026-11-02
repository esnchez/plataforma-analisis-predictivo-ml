"""HTTP read API over the DynamoDB scores and the model performance report: what the frontend calls and
k6 load-tests.

It only reads; scores are written by the scoring job, the report by the monitoring job.
"""

import time
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response

from churn.api import metrics
from churn.api.lookup import (count_customers_by_risk, get_customer_score, get_model_performance,
                              list_customers_by_risk)
from churn.shared.aws import dynamodb_resource, s3_client
from churn.shared.config import get_settings

app = FastAPI(title="Churn predictions API")


@lru_cache
def _table():
    settings = get_settings()
    return dynamodb_resource(settings).Table(settings.table)


@lru_cache
def _s3():
    return s3_client(get_settings())


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Label by route template (/customers/{customer_id}/churn), not by raw path: one series per
        # endpoint instead of one per customer.
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            metrics.REQUEST_DURATION.labels(request.method, path, str(status)).observe(time.perf_counter() - start)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)


@app.get("/customers/{customer_id}/churn")
def customer_churn(customer_id: str):
    with metrics.time_dynamodb("GetItem"):
        item = get_customer_score(_table(), customer_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No active churn score for {customer_id}")
    return item


@app.get("/churn/at-risk")
def at_risk(band: Literal["high", "medium", "low"] = "high", limit: int = Query(50, ge=1, le=500)):
    with metrics.time_dynamodb("Query"):
        customers = list_customers_by_risk(_table(), band, limit)
    return {"band": band, "customers": customers}


@app.get("/churn/summary")
def summary():
    with metrics.time_dynamodb("QueryCount"):
        counts = count_customers_by_risk(_table())
    return {"customers": sum(counts.values()), "by_band": counts}


@app.get("/model/performance")
def model_performance():
    report = get_model_performance(_s3(), get_settings().bucket)
    if report is None:
        raise HTTPException(status_code=404, detail="No performance report yet: the monitoring job hasn't run")
    return report
