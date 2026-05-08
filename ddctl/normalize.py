from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from dateutil import parser as dateutil_parser


MAX_NAME = 60
MAX_QUERY = 80
MAX_MSG = 200
MAX_RESOURCE = 80


def trunc(s: Any, limit: int) -> Any:
    if s is None:
        return s
    s = str(s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def coerce_attrs_map(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        out: Dict[str, Any] = {}
        for el in obj:
            if isinstance(el, dict):
                if "key" in el and "value" in el:
                    out[str(el["key"])] = el["value"]
                else:
                    for k, v in el.items():
                        out.setdefault(str(k), v)
            elif isinstance(el, str) and ":" in el:
                k, v = el.split(":", 1)
                out[k.strip()] = v.strip()
        return out
    return {}


def ts_to_iso(ts_raw: Any) -> str:
    if isinstance(ts_raw, (int, float)):
        v = float(ts_raw)
        if v > 1e15:
            v /= 1e9
        elif v > 1e12:
            v /= 1e3
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return str(ts_raw)
    if isinstance(ts_raw, str) and ts_raw:
        try:
            dt = dateutil_parser.parse(ts_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return ts_raw
    return ""


def duration_to_ms(value: Any) -> float:
    try:
        v = float(value or 0)
    except Exception:
        return 0.0
    if v > 10_000_000:
        return round(v / 1_000_000.0, 2)
    if v > 10_000:
        return round(v / 1000.0, 2)
    if v <= 10:
        return round(v * 1000.0, 2)
    return round(v, 2)


def normalize_monitor(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "name": trunc(raw.get("name", ""), MAX_NAME),
        "state": raw.get("overall_state") or raw.get("overallState"),
        "type": raw.get("type"),
        "priority": raw.get("priority"),
    }


def normalize_log(raw: dict) -> dict:
    attrs = (raw or {}).get("attributes") or {}
    nested = attrs.get("attributes") or {}
    return {
        "ts": ts_to_iso(attrs.get("timestamp")),
        "service": nested.get("service") or attrs.get("service") or "",
        "status": attrs.get("status") or "",
        "msg": trunc(nested.get("message") or attrs.get("message") or "", MAX_MSG),
    }


def normalize_span(raw: dict) -> dict:
    attrs = (raw or {}).get("attributes") or {}
    nested: Dict[str, Any] = {}
    nested.update(coerce_attrs_map(attrs.get("attributes")))
    nested.update(coerce_attrs_map(attrs.get("custom")))
    nested.update(coerce_attrs_map(attrs.get("tags")))
    ts_raw = attrs.get("timestamp") or attrs.get("start_timestamp") or attrs.get("start") or ""
    resource = (
        nested.get("resource_name")
        or nested.get("resource.name")
        or attrs.get("resource_name")
        or attrs.get("resource")
        or nested.get("resource")
        or ""
    )
    http_status = (
        nested.get("http.status_code")
        or nested.get("status_code")
        or attrs.get("status")
        or nested.get("status")
        or ""
    )
    err = (
        nested.get("error.message")
        or nested.get("error.type")
        or nested.get("error")
        or nested.get("error.msg")
        or ""
    )
    out = {
        "ts": ts_to_iso(ts_raw),
        "service": nested.get("service") or attrs.get("service") or "",
        "resource": trunc(resource, MAX_RESOURCE),
        "http_status": str(http_status) if http_status else "",
        "dur_ms": duration_to_ms(attrs.get("duration") or nested.get("duration") or 0),
    }
    if err:
        out["err"] = trunc(err, MAX_MSG)
    return out


def normalize_dashboard_summary(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "title": trunc(raw.get("title", ""), MAX_NAME),
        "modified_at": raw.get("modified_at"),
    }


def normalize_dashboard_detail(raw: dict) -> dict:
    return {
        "id": raw.get("id"),
        "title": trunc(raw.get("title", ""), MAX_NAME),
        "url": raw.get("url"),
        "modified_at": raw.get("modified_at"),
        "widget_count": len(raw.get("widgets") or []),
    }


def normalize_incident(raw: dict) -> dict:
    attrs = raw.get("attributes") or {}
    return {
        "id": raw.get("id"),
        "title": trunc(attrs.get("title") or "", MAX_NAME),
        "severity": attrs.get("severity"),
        "state": attrs.get("state"),
        "created": attrs.get("created"),
    }


def normalize_synthetic_trigger(raw: dict) -> dict:
    results = raw.get("results") or []
    return {
        "batch_id": raw.get("batch_id"),
        "triggered_count": len(results),
        "locations": sorted({r.get("location") for r in results if r.get("location")}),
    }


def normalize_catalog_entity(raw: dict) -> dict:
    attrs = raw.get("attributes") or {}
    schema = raw.get("included_schema") or {}
    return {
        "name": attrs.get("name", ""),
        "owner": attrs.get("owner", ""),
        "tier": (schema.get("spec") or {}).get("tier", ""),
        "tags": attrs.get("tags") or [],
    }


def normalize_rum_application(raw: dict) -> dict:
    attrs = raw.get("attributes") or {}
    return {
        "id": attrs.get("application_id") or raw.get("id"),
        "name": trunc(attrs.get("name", ""), MAX_NAME),
        "type": attrs.get("type"),
    }


def normalize_rum_event(raw: dict) -> dict:
    attrs = (raw or {}).get("attributes") or {}
    nested = attrs.get("attributes") or {}
    view = nested.get("view") or {}
    error = nested.get("error") or {}
    return {
        "ts": ts_to_iso(attrs.get("timestamp")),
        "type": nested.get("type") or attrs.get("type") or "",
        "session_id": (nested.get("session") or {}).get("id"),
        "view_url": trunc(view.get("url") or "", MAX_RESOURCE),
        "error_msg": trunc(error.get("message") or "", MAX_MSG) if error else None,
    }


def normalize_host(raw: dict) -> dict:
    tags = raw.get("tags_by_source") or {}
    relevant: List[str] = []
    flat: List[str] = []
    if isinstance(tags, dict):
        for v in tags.values():
            if isinstance(v, list):
                flat.extend(v)
    for t in flat:
        if any(t.startswith(p) for p in ("env:", "service:", "cluster:", "kube_cluster_name:", "role:")):
            relevant.append(t)
    return {
        "name": raw.get("name") or raw.get("host_name"),
        "up": raw.get("up"),
        "tags_relevant": sorted(set(relevant)),
        "apps": raw.get("apps") or [],
        "last_seen": raw.get("last_reported_time"),
        "muted": raw.get("is_muted"),
    }


def normalize_downtime(raw: dict) -> dict:
    attrs = raw.get("attributes") or {}
    schedule = attrs.get("schedule") or {}
    return {
        "id": raw.get("id"),
        "scope": attrs.get("scope"),
        "active": attrs.get("status") == "active",
        "start": schedule.get("start") or attrs.get("start"),
        "end": schedule.get("end") or attrs.get("end"),
        "message": trunc(attrs.get("message") or "", MAX_MSG),
    }


def extract_buckets(resp: dict) -> List[dict]:
    if not isinstance(resp, dict):
        return []
    data = resp.get("data")
    if isinstance(data, dict):
        return ((data.get("attributes") or {}).get("buckets") or [])
    if isinstance(data, list):
        return data
    return (resp.get("attributes") or {}).get("buckets") or []


def bucket_count(b: dict) -> int:
    ref = b.get("attributes") or b
    compute = ref.get("compute") or {}
    if isinstance(compute, dict) and "c0" in compute:
        try:
            return int(compute.get("c0") or 0)
        except Exception:
            return 0
    computes = ref.get("computes") or [{}]
    try:
        return int((computes[0] or {}).get("value") or 0)
    except Exception:
        return 0


def aggregate_compute(resp: dict) -> Dict[str, Any]:
    buckets = extract_buckets(resp)
    if buckets:
        ref = (buckets[0] or {}).get("attributes") or buckets[0] or {}
        compute = ref.get("compute") or {}
        if isinstance(compute, dict) and any(str(k).startswith("c") for k in compute):
            return compute
        computes = ref.get("computes")
        if isinstance(computes, list):
            return {f"c{i}": (e or {}).get("value") for i, e in enumerate(computes)}
    return {}


DEFAULT_FIELDS: Dict[str, List[str]] = {
    "auth.status": ["site", "api_key_valid"],
    "monitors.list": ["id", "name", "state", "priority"],
    "monitors.mute": ["id", "name", "muted"],
    "logs.query": ["ts", "service", "status", "msg"],
    "spans.list": ["ts", "service", "resource", "http_status", "dur_ms", "err"],
    "spans.search": ["ts", "service", "resource", "http_status", "dur_ms", "err"],
    "errors.top_resources": ["resource", "count"],
    "errors.rate": ["key", "count"],
    "service.troubleshoot": [
        "service", "env", "window", "err_rate_pct", "req_total", "req_err",
        "lat_ms", "top_err_resources", "top_err_msgs",
    ],
    "dashboards.get": ["id", "title", "url", "modified_at", "widget_count"],
    "incidents.create": ["id", "title", "severity", "state"],
    "synthetics.trigger": ["batch_id", "triggered_count", "locations"],
    "metrics.query": ["metric", "scope", "n_points", "last_ts", "last", "avg", "min", "max"],
    "metrics.k8s": ["resource", "requests", "limits", "usage"],
    "metrics.tag_cardinality": ["tag_key", "cardinality"],
    "services.list": ["name", "owner", "tier", "tags"],
    "services.get": ["name", "owner", "tier", "tags"],
    "services.apply": ["name", "applied"],
    "rum.apps.list": ["id", "name", "type"],
    "rum.events.search": ["ts", "type", "session_id", "view_url", "error_msg"],
    "rum.events.count": ["group", "count"],
    "hosts.list": ["name", "up", "tags_relevant", "apps", "last_seen", "muted"],
    "hosts.count": ["total_up", "total_active", "total"],
    "hosts.mute": ["host", "muted", "end"],
    "hosts.unmute": ["host", "muted"],
    "downtimes.list": ["id", "scope", "active", "start", "end", "message"],
    "downtimes.schedule": ["id", "scope", "start", "end", "message"],
    "downtimes.cancel": ["id", "cancelled"],
}
