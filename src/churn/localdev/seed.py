"""Seed job (local only): create the bucket and table, then land synthetic source data in S3 raw/.

Stands in for Terraform and for the real source systems (CRM, product, support, billing). Runs once and
exits. Re-running it rewrites the same objects (same seed, same keys), so it is safe to repeat.
    python -m churn.localdev [--customers 2000 --months 24 --last-month 2026-09 --seed 42]
"""

import argparse

from churn.localdev import synthetic
from churn.localdev.args import add_seed_arguments
from churn.localdev.setup_resources import ensure_bucket, ensure_table
from churn.shared import raw_data
from churn.shared.aws import dynamodb_resource, s3_client
from churn.shared.config import Settings, get_settings


def run(settings: Settings, customers: int = 2000, months: int = 24, last_month: str = "2026-09",
        seed: int = 42) -> str:
    s3 = s3_client(settings)
    ensure_bucket(s3, settings.bucket, settings.region)
    ensure_table(dynamodb_resource(settings), settings.table)
    batches = synthetic.generate(customers, months, last_month, seed)
    for batch in batches:
        raw_data.ingest_records(s3, settings.bucket, batch.source, batch.partition, batch.records)
    total = sum(len(b.records) for b in batches)
    return (f"Bucket {settings.bucket} and table {settings.table} ready; wrote {total} records "
            f"in {len(batches)} objects under s3://{settings.bucket}/raw/")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m churn.localdev")
    add_seed_arguments(parser)
    args = parser.parse_args(argv)
    print(run(get_settings(), args.customers, args.months, args.last_month, args.seed))
