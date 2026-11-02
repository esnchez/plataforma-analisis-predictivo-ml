"""Prometheus metrics for the API, scraped by Prometheus from GET /metrics and charted in Grafana.

Two histograms: whole requests by route, and the DynamoDB calls inside them. Comparing the two shows
where time goes (in the load tests: almost all of it in DynamoDB).

uvicorn runs several worker processes. With PROMETHEUS_MULTIPROC_DIR set they write their metrics to
files in that directory, and /metrics reports the sum over all workers.
"""

import os
import shutil
import time
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, Histogram, generate_latest, multiprocess

# Seconds. Dense around the 150 ms p99 target.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.25, 0.5, 1, 2.5, 5, 10)

REQUEST_DURATION = Histogram("churn_api_request_duration_seconds", "HTTP request duration",
                             ["method", "route", "status"], buckets=BUCKETS)
DYNAMODB_DURATION = Histogram("churn_api_dynamodb_duration_seconds", "DynamoDB call duration inside requests",
                              ["operation"], buckets=BUCKETS)


@contextmanager
def time_dynamodb(operation: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        DYNAMODB_DURATION.labels(operation).observe(time.perf_counter() - start)


def reset_multiprocess_dir() -> None:
    """Called once before the workers start: metric files from a previous run must not be summed in."""
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if directory:
        shutil.rmtree(directory, ignore_errors=True)
        os.makedirs(directory)


def render() -> tuple[bytes, str]:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return generate_latest(registry), CONTENT_TYPE_LATEST
