#!/usr/bin/env python3
# SPDX-License-Identifier: MIT-0
"""Live Metrics API — CloudWatch-powered real-time data for the Connect Operations Dashboard.

This Lambda function serves as the backend for the dashboard's live refresh.
It queries CloudWatch for current-hour and today's metrics, calculates TPS,
and returns the data in the exact shape the dashboard JS expects.

Endpoints (via API Gateway):
    GET /metrics?view=today    → Full day metrics (default)
    GET /metrics?view=hour     → Current hour only (fast)
    GET /metrics?view=week     → 7-day historical

Environment Variables:
    CONNECT_INSTANCE_ID: Connect instance ID
    LINE_CONFIG_S3_BUCKET: S3 bucket containing line-config.json (optional)
    LINE_CONFIG_S3_KEY: S3 key for line-config.json (optional)
    LINE_CONFIG_JSON: Inline JSON config (alternative to S3, for small configs)

Required IAM Permissions:
    cloudwatch:GetMetricData
    cloudwatch:GetMetricStatistics
    servicequotas:GetServiceQuota
    s3:GetObject (if using S3 config)

Author: Amazon.com, Inc.
License: MIT-0
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

BUSINESS_DAY_SECONDS = 28_800
CONNECT_SERVICE_CODE = "connect"

HIGH_TRAFFIC_APIS = (
    "GetContactAttributes",
    "UpdateContactAttributes",
    "DescribeContact",
    "GetMetricDataV2",
    "GetCurrentMetricData",
    "StartOutboundVoiceContact",
    "StopContact",
    "TransferContact",
    "TagContact",
    "SearchContacts",
)

# Standard contact center hourly distribution (pct of daily per hour, 0-23)
HOURLY_DISTRIBUTION = [
    0, 0, 0, 0, 0, 0.4, 2.8, 7.5, 12.0, 14.0, 13.5, 12.0,
    11.5, 10.0, 9.0, 7.5, 5.0, 2.5, 1.2, 0.4, 0.1, 0, 0, 0
]

# ═══════════════════════════════════════════════════════════════════════════════
# HANDLER
# ═══════════════════════════════════════════════════════════════════════════════


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for API Gateway proxy integration.

    Args:
        event: API Gateway event with queryStringParameters.
        context: Lambda context object.

    Returns:
        API Gateway response with CORS headers and JSON body.
    """
    try:
        params = event.get("queryStringParameters") or {}
        view = params.get("view", "today")

        instance_id = os.environ.get("CONNECT_INSTANCE_ID", "")
        if not instance_id:
            return _response(400, {"error": "CONNECT_INSTANCE_ID not configured"})

        line_config = _load_line_config()
        metrics_data = _collect_live_metrics(view, instance_id)
        quota_data = _collect_quota_snapshot()

        result = _build_response_payload(view, metrics_data, quota_data, line_config)

        return _response(200, result)

    except Exception as e:
        logger.exception("Unhandled error")
        return _response(500, {"error": str(e)})


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG LOADING
# ═══════════════════════════════════════════════════════════════════════════════


def _load_line_config() -> dict[str, Any]:
    """Load line configuration from environment or S3."""
    # Try inline JSON first (fastest)
    inline = os.environ.get("LINE_CONFIG_JSON", "")
    if inline:
        return json.loads(inline)

    # Try S3
    bucket = os.environ.get("LINE_CONFIG_S3_BUCKET", "")
    key = os.environ.get("LINE_CONFIG_S3_KEY", "line-config.json")
    if bucket:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))

    # Fallback: single default line
    return {
        "lines": [{"id": "all", "name": "All Lines", "number": "", "match": {"flow_patterns": ["*"], "number_prefixes": []}}],
        "defaults": {"business_day_hours": 8, "contacts_per_number_per_day": 15, "apis_per_contact": 18, "growth_projection_pct": 25},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLOUDWATCH COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════


def _collect_live_metrics(view: str, instance_id: str) -> dict[str, Any]:
    """Query CloudWatch for live Connect metrics.

    Args:
        view: One of 'today', 'hour', 'week'.
        instance_id: Connect instance ID.

    Returns:
        Dict with per-API metrics and volume data.
    """
    cw = boto3.client("cloudwatch")
    now = datetime.now(timezone.utc)

    if view == "hour":
        start = now.replace(minute=0, second=0, microsecond=0)
        end = now
        period = 60  # 1-minute granularity for current hour
    elif view == "week":
        start = now - timedelta(days=7)
        end = now
        period = 86_400  # Daily granularity
    else:  # today
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = now
        period = 3_600  # Hourly granularity for today

    # Build metric queries for all high-traffic APIs
    queries = []
    for i, api_name in enumerate(HIGH_TRAFFIC_APIS):
        queries.append({
            "Id": f"api_{i}",
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/Usage",
                    "MetricName": "CallCount",
                    "Dimensions": [
                        {"Name": "Service", "Value": "Connect"},
                        {"Name": "Type", "Value": "API"},
                        {"Name": "Resource", "Value": api_name},
                        {"Name": "Class", "Value": "None"},
                    ],
                },
                "Period": period,
                "Stat": "Sum",
            },
        })

    # Also get concurrent calls metric (actual contact volume)
    queries.append({
        "Id": "concurrent",
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/Connect",
                "MetricName": "ConcurrentCalls",
                "Dimensions": [
                    {"Name": "InstanceId", "Value": instance_id},
                    {"Name": "MetricGroup", "Value": "VoiceCalls"},
                ],
            },
            "Period": period,
            "Stat": "Maximum",
        },
    })

    queries.append({
        "Id": "calls_incoming",
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/Connect",
                "MetricName": "CallsIncoming",
                "Dimensions": [
                    {"Name": "InstanceId", "Value": instance_id},
                    {"Name": "MetricGroup", "Value": "VoiceCalls"},
                ],
            },
            "Period": period,
            "Stat": "Sum",
        },
    })

    # Execute (max 500 queries per call, we have ~13)
    try:
        response = cw.get_metric_data(
            MetricDataQueries=queries,
            StartTime=start,
            EndTime=end,
        )
    except ClientError as e:
        logger.error("CloudWatch query failed: %s", e)
        return {"error": str(e), "apis": {}, "volume": {}}

    # Parse results
    results: dict[str, Any] = {"apis": {}, "volume": {}}

    for metric_result in response.get("MetricDataResults", []):
        metric_id = metric_result["Id"]
        values = metric_result.get("Values", [])
        timestamps = metric_result.get("Timestamps", [])

        if metric_id.startswith("api_"):
            idx = int(metric_id.split("_")[1])
            api_name = HIGH_TRAFFIC_APIS[idx]
            total = sum(values)
            peak = max(values) if values else 0

            results["apis"][api_name] = {
                "total_calls": total,
                "peak_period_calls": peak,
                "values": values,
                "timestamps": [t.isoformat() for t in timestamps],
                "current_tps": peak / period if period > 0 else 0,
            }

        elif metric_id == "concurrent":
            results["volume"]["concurrent_peak"] = max(values) if values else 0
            results["volume"]["concurrent_values"] = values

        elif metric_id == "calls_incoming":
            results["volume"]["total_calls"] = sum(values)
            results["volume"]["hourly_values"] = values
            results["volume"]["timestamps"] = [t.isoformat() for t in timestamps]

    results["view"] = view
    results["period"] = period
    results["start"] = start.isoformat()
    results["end"] = end.isoformat()

    return results


def _collect_quota_snapshot() -> dict[str, Any]:
    """Get current service quota limits (cached — quotas don't change often)."""
    sq = boto3.client("service-quotas")
    quotas: dict[str, Any] = {}

    try:
        paginator = sq.get_paginator("list_service_quotas")
        for page in paginator.paginate(ServiceCode=CONNECT_SERVICE_CODE):
            for quota in page.get("Quotas", []):
                name = quota.get("QuotaName", "")
                api_name = name.replace("Rate of ", "").replace(" API requests", "")
                if api_name in HIGH_TRAFFIC_APIS:
                    quotas[api_name] = {
                        "limit": quota.get("Value", 0),
                        "code": quota.get("QuotaCode", ""),
                    }
    except ClientError as e:
        logger.warning("Service Quotas query failed: %s", e)

    return quotas


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def _build_response_payload(
    view: str,
    metrics: dict[str, Any],
    quotas: dict[str, Any],
    line_config: dict[str, Any],
) -> dict[str, Any]:
    """Build the response payload matching the dashboard's expected data shape.

    Returns a partial update that the dashboard JS merges into its state.
    """
    now = datetime.now(timezone.utc)

    # Build SYSTEM_API_USAGE (current state)
    system_api_usage: dict[str, Any] = {}
    for api_name, api_data in metrics.get("apis", {}).items():
        limit = quotas.get(api_name, {}).get("limit", 0)
        current_tps = api_data.get("current_tps", 0)
        system_api_usage[api_name] = {
            "total": round(current_tps, 2),
            "limit": limit,
            "utilization_pct": round(current_tps / limit * 100, 1) if limit > 0 else 0,
        }

    # Build volume summary
    volume = metrics.get("volume", {})
    total_calls = volume.get("total_calls", 0)
    hourly_values = volume.get("hourly_values", [])

    # Pad hourly to 24 values (fill future hours with 0)
    current_hour = now.hour
    hourly_padded = [0] * 24
    for i, val in enumerate(reversed(hourly_values)):
        hour_idx = current_hour - i
        if 0 <= hour_idx < 24:
            hourly_padded[hour_idx] = int(val)

    # Find limiting API
    max_util = 0
    limiting_api = "Unknown"
    for name, usage in system_api_usage.items():
        if usage.get("utilization_pct", 0) > max_util:
            max_util = usage["utilization_pct"]
            limiting_api = name

    # Build per-line breakdown (proportional to config)
    lines = line_config.get("lines", [])
    line_count = len(lines)
    lines_data = []

    for i, line_cfg in enumerate(lines):
        # Distribute total calls across lines (equal split as baseline)
        line_vol = int(total_calls / max(line_count, 1))
        line_hour = int(hourly_padded[current_hour] / max(line_count, 1)) if current_hour < 24 else 0

        lines_data.append({
            "id": line_cfg["id"],
            "name": line_cfg["name"],
            "today": line_vol,
            "hour": line_hour,
            "hourly": hourly_padded,
            "capacityPct": min(int(max_util), 99),
        })

    # Total capacity
    total_capacity = {
        "maxCallsPerDay": int(total_calls / (max_util / 100)) if max_util > 0 else total_calls * 2,
        "currentDaily": total_calls,
        "headroomCalls": int(total_calls / (max_util / 100)) - total_calls if max_util > 0 else total_calls,
        "limitingApi": limiting_api,
        "limitingPct": int(max_util),
    }

    return {
        "view": view,
        "timestamp": now.isoformat(),
        "LINES": lines_data,
        "SYSTEM_API_USAGE": system_api_usage,
        "TOTAL_CAPACITY": total_capacity,
        "volume": {
            "total_calls_today": total_calls,
            "concurrent_peak": volume.get("concurrent_peak", 0),
            "hourly": hourly_padded,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build API Gateway proxy response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Cache-Control": "no-cache, max-age=0" if status_code == 200 else "no-store",
        },
        "body": json.dumps(body, default=str),
    }
