"""S3 read/write helpers for the batch services (JSON Lines, Parquet, JSON)."""

import io
import json

import pandas as pd


def list_keys(s3, bucket: str, prefix: str) -> list[str]:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return sorted(keys)


def put_jsonl(s3, bucket: str, key: str, records: list[dict]) -> None:
    body = "\n".join(json.dumps(r, default=str) for r in records).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")


def read_jsonl_prefix(s3, bucket: str, prefix: str) -> pd.DataFrame:
    """Read every JSON Lines object under a prefix into one DataFrame."""
    frames = []
    for key in list_keys(s3, bucket, prefix):
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if body.strip():
            frames.append(pd.read_json(io.BytesIO(body), lines=True, dtype=False))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def put_parquet(s3, bucket: str, key: str, df: pd.DataFrame) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False)
    s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())


def read_parquet(s3, bucket: str, key: str) -> pd.DataFrame:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def put_json(s3, bucket: str, key: str, obj: dict) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(obj, indent=2, default=str).encode("utf-8"),
                  ContentType="application/json")


def read_json(s3, bucket: str, key: str) -> dict:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
