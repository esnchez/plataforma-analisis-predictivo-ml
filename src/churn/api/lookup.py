"""Read side: churn scores from DynamoDB and the model performance report from S3. Used by the API
and the CLI. boto3 only, no pandas."""

import json
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from churn.shared.config import PERFORMANCE_KEY, RISK_BAND_INDEX, RISK_BAND_NAMES


def _from_dynamodb(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_from_dynamodb(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamodb(v) for k, v in value.items()}
    return value


def get_customer_score(table, customer_id: str) -> dict | None:
    item = table.get_item(Key={"CustomerID": customer_id}).get("Item")
    return _from_dynamodb(item) if item else None


def list_customers_by_risk(table, band: str = "high", limit: int = 50) -> list[dict]:
    """Customers in a risk band, highest probability first (GSI query, no table scan)."""
    response = table.query(
        IndexName=RISK_BAND_INDEX,
        KeyConditionExpression=Key("risk_band").eq(band),
        ScanIndexForward=False,
        Limit=limit,
    )
    return [_from_dynamodb(item) for item in response["Items"]]


def count_customers_by_risk(table) -> dict[str, int]:
    """Number of scored customers per risk band (COUNT queries on the GSI: no items are transferred)."""
    counts = {}
    for band in RISK_BAND_NAMES:
        kwargs = {"IndexName": RISK_BAND_INDEX, "KeyConditionExpression": Key("risk_band").eq(band),
                  "Select": "COUNT"}
        counts[band] = 0
        while True:
            page = table.query(**kwargs)
            counts[band] += page["Count"]
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return counts


def get_model_performance(s3, bucket: str) -> dict | None:
    """The report written by the monitoring service, or None if it hasn't run yet."""
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=PERFORMANCE_KEY)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
