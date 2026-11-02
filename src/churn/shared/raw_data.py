"""Raw data landing zone in S3.

Every source system (CRM, product analytics, support desk, billing) writes JSON Lines files under
`raw/<source>/<partition>/<batch_id>.jsonl`. Nothing is overwritten by date: each period gets its own
partition, so the full history stays available for training. See docs/arquitectura.md.
"""

import pandas as pd

from churn.shared.s3_io import list_keys, put_jsonl, read_jsonl_prefix

SOURCES = ("crm_customers", "usage", "support_tickets", "billing_events")


def raw_key(source: str, partition: str, batch_id: str) -> str:
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}, expected one of {SOURCES}")
    return f"raw/{source}/{partition}/{batch_id}.jsonl"


def ingest_records(s3, bucket: str, source: str, partition: str, records: list[dict],
                   batch_id: str = "part-0000") -> str:
    """Write one batch of records for a source and partition. Same batch_id = idempotent re-upload."""
    key = raw_key(source, partition, batch_id)
    put_jsonl(s3, bucket, key, records)
    return key


def load_source(s3, bucket: str, source: str) -> pd.DataFrame:
    """Read every partition of an append-only source (usage, tickets, billing)."""
    return read_jsonl_prefix(s3, bucket, f"raw/{source}/")


def load_latest_partition(s3, bucket: str, source: str) -> pd.DataFrame:
    """Read only the newest partition of a snapshot source (the CRM exports a full table each time)."""
    keys = list_keys(s3, bucket, f"raw/{source}/")
    if not keys:
        return pd.DataFrame()
    latest_partition = max(key.split("/")[2] for key in keys)
    return read_jsonl_prefix(s3, bucket, f"raw/{source}/{latest_partition}/")
