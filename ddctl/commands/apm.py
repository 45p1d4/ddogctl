from __future__ import annotations

import sys
from typing import Optional, List

import typer
from rich.console import Console
from rich.table import Table
from rich.json import JSON as RichJSON
from datetime import datetime, timezone
from dateutil import parser as dateutil_parser

from ..cli import get_client_from_ctx
from ..api import ApiError
from ..utils_time import parse_time, to_iso8601
from ..i18n import t
from ..normalize import (
    bucket_count as _bucket_count,
    extract_buckets as _norm_extract_buckets,
    normalize_span,
    normalize_trace_span,
    trunc as _trunc,
    ts_to_iso as _ts_to_iso,
)
from ..ui import emit, new_table, build_title

app = typer.Typer(help=t("Operaciones de APM", "APM operations"))
console = Console()

spans_app = typer.Typer(help=t("Operaciones sobre Spans", "Spans operations"))
errors_app = typer.Typer(help=t("Reportes de errores", "Error analytics"))
trace_app = typer.Typer(help=t("Operaciones sobre Traces", "Trace operations"))
app.add_typer(spans_app, name="spans")
app.add_typer(errors_app, name="errors")
app.add_typer(trace_app, name="trace")


def _coerce_attrs_map(obj) -> dict:
    """
    Normaliza estructuras de atributos/tags a un dict:
    - dict -> dict
    - lista de {key,value} -> {key: value}
    - lista de strings "k:v" -> {k: v}
    """
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        result = {}
        for el in obj:
            if isinstance(el, dict):
                if "key" in el and "value" in el:
                    result[str(el["key"])] = el["value"]
                else:
                    for k, v in el.items():
                        if k not in result:
                            result[str(k)] = v
            elif isinstance(el, str) and ":" in el:
                k, v = el.split(":", 1)
                result[k.strip()] = v.strip()
        return result
    return {}


def _build_query(service: Optional[str], extra: Optional[str], env: Optional[str] = None) -> str:
    terms: List[str] = []
    if service:
        terms.append(f"service:{service}")
    if env:
        terms.append(f"env:{env}")
    if extra:
        terms.append(extra)
    return " ".join(terms) if terms else "*"


def _extract_buckets(resp: dict) -> List[dict]:
    if not isinstance(resp, dict):
        return []
    data = resp.get("data")
    if isinstance(data, dict):
        attrs = data.get("attributes") or {}
        buckets = attrs.get("buckets") or []
        return buckets if isinstance(buckets, list) else []
    if isinstance(data, list):
        # Some APIs may return buckets directly as list under data
        return data
    # Fallback: try attributes at top-level
    attrs = resp.get("attributes") or {}
    buckets = attrs.get("buckets") or []
    return buckets if isinstance(buckets, list) else []


def _format_ts_parts(ts_raw) -> tuple[str, str]:
    if isinstance(ts_raw, (int, float)):
        try:
            # assume ns
            dt = datetime.fromtimestamp(float(ts_raw) / 1_000_000_000.0, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except Exception:
            return "", str(ts_raw)
    if isinstance(ts_raw, str) and ts_raw:
        try:
            dt = dateutil_parser.parse(ts_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except Exception:
            return "", ts_raw
    return "", ""


def _render_spans_table(items: List[dict]) -> None:
    processed_rows = []
    env_set = set()
    service_set = set()
    date_set = set()

    for item in items:
        attrs = (item or {}).get("attributes") or {}
        # Span tags can live under attributes.attributes or attributes.tags; sometimes lists
        nested = {}
        nested.update(_coerce_attrs_map(attrs.get("attributes")))
        nested.update(_coerce_attrs_map(attrs.get("custom")))
        nested.update(_coerce_attrs_map(attrs.get("tags")))
        # Timestamp: some payloads provide 'timestamp' (ISO) and others 'start' in ns
        ts_raw = (
            attrs.get("timestamp")
            or attrs.get("start_timestamp")
            or attrs.get("start")
            or ""
        )
        date_str, time_str = _format_ts_parts(ts_raw)
        if date_str:
            date_set.add(date_str)
        env = nested.get("env") or attrs.get("env") or ""
        service = nested.get("service") or attrs.get("service") or ""
        # Resource can appear as resource, resource_name or resource.name
        resource = (
            nested.get("resource_name")
            or nested.get("resource.name")
            or attrs.get("resource_name")
            or attrs.get("resource")
            or nested.get("resource")
            or ""
        )
        method = (
            nested.get("http.method")
            or nested.get("method")
            or attrs.get("operation_name")  # fallback to operation name
            or ""
        )
        status = (
            nested.get("http.status_code")
            or nested.get("status_code")
            or attrs.get("status")
            or nested.get("status")
            or ""
        )
        # Duration can be in ns (common). Fall back to ms if provided.
        duration = (
            attrs.get("duration")
            or nested.get("duration")
            or nested.get("duration.ms")
            or 0
        )
        try:
            # Many span durations are ns; convert to ms if duration seems large
            d = float(duration)
            duration_s = d / 1_000_000_000.0 if d > 10_000_000 else d / 1000.0
        except Exception:
            duration_s = 0.0
        error_msg = (
            nested.get("error.message")
            or nested.get("error.type")
            or nested.get("error")
            or nested.get("error.msg")
            or ""
        )
        processed_rows.append((time_str, env, service, resource, method, status, f"{duration_s:.3f}", str(error_msg)[:120]))
        if env:
            env_set.add(env)
        if service:
            service_set.add(service)

    # Title composition with common env/service/date
    metadata = {}
    if len(date_set) == 1:
        metadata["date"] = next(iter(date_set))
    if len(env_set) == 1:
        metadata["env"] = next(iter(env_set))
    if len(service_set) == 1:
        metadata["service"] = next(iter(service_set))

    table = new_table("Spans", metadata)
    table.add_column("timestamp", style="cyan", no_wrap=True)
    # Determine column presence (hide columns that are blank across all rows)
    any_env = any(r[1] for r in processed_rows)
    any_service = any(r[2] for r in processed_rows)
    any_resource = any(r[3] for r in processed_rows)
    any_method = any(r[4] for r in processed_rows)
    any_status = any(r[5] for r in processed_rows)
    any_duration = any(r[6] for r in processed_rows)
    any_error = any(r[7] for r in processed_rows)

    # Only include env/service columns if they are not constant and not blank
    if len(env_set) != 1 and any_env:
        table.add_column("env", style="blue", no_wrap=True)
    if len(service_set) != 1 and any_service:
        table.add_column("service", style="magenta", no_wrap=True)
    if any_resource:
        table.add_column("resource", style="white")
    if any_method:
        table.add_column("method", style="green", no_wrap=True)
    if any_status:
        table.add_column("status", style="green", no_wrap=True)
    if any_duration:
        table.add_column("duration_s", style="yellow", no_wrap=True)
    if any_error:
        table.add_column("error_message", style="red")

    for ts, env, service, resource, method, status, duration_ms, error_msg in processed_rows:
        row = [ts]
        if len(env_set) != 1 and any_env:
            row.append(env)
        if len(service_set) != 1 and any_service:
            row.append(service)
        if any_resource:
            row.append(resource)
        if any_method:
            row.append(method)
        if any_status:
            row.append(status)
        if any_duration:
            row.append(duration_ms)
        if any_error:
            row.append(error_msg)
        table.add_row(*row)

    console.print(table)


@spans_app.command(
    "list",
    help=t(
        "GET /api/v2/spans/events con filtros simples por servicio y tiempo",
        "GET /api/v2/spans/events with simple service/time filters",
    ),
)
def spans_list(
    ctx: typer.Context,
    service: Optional[str] = typer.Option(None, "--service", help=t("Filtrar por service", "Filter by service")),
    env: Optional[str] = typer.Option(None, "--env", help=t("Filtrar por env (p.ej. prd/dev)", "Filter by env (e.g., prd/dev)")),
    from_: str = typer.Option("now-15m", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    query: Optional[str] = typer.Option(None, "--query", help=t("Consulta adicional", "Additional query")),
    sort: str = typer.Option("-timestamp", "--sort", help="Sort", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP error details"),
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        params = {
            "filter[query]": _build_query(service, query, env),
            "filter[from]": to_iso8601(dt_from),
            "filter[to]": to_iso8601(dt_to),
            "page[limit]": limit,
            "sort": sort,
        }
        with console.status("[dim]Cargando spans[/dim]"):
            data = client.get("/api/v2/spans/events", params=params) or {}
        items = data.get("data") or []
        if debug and items:
            console.rule("raw item (GET /spans/events)")
            console.print(RichJSON.from_data(items[0]))
        normalized = [normalize_span(it) for it in items]
        meta = {"from": from_, "to": to, "query": params["filter[query]"]}
        emit(
            ctx,
            "spans.list",
            normalized,
            raw=items,
            meta=meta,
            table_renderer=lambda: _render_spans_table(items),
        )
    except Exception as exc:
        if debug:
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
            else:
                console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@spans_app.command(
    "search",
    help=t(
        "POST /api/v2/spans/events/search con consulta avanzada",
        "POST /api/v2/spans/events/search with advanced query",
    ),
)
def spans_search(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Consulta de spans (Trace Explorer)", "Span query (Trace Explorer)")),
    env: Optional[str] = typer.Option(None, "--env", help=t("Filtrar por env (p.ej. prd/dev)", "Filter by env (e.g., prd/dev)")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    sort: str = typer.Option("-timestamp", "--sort", help="Sort", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP error details"),
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        effective_query = _build_query(None, query, env) if env else (query or "*")
        payload = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {
                        "from": to_iso8601(dt_from),
                        "to": to_iso8601(dt_to),
                        "query": effective_query,
                    },
                    "page": {"limit": limit},
                    "sort": sort,
                },
            }
        }
        with console.status("[dim]Buscando spans[/dim]"):
            data = client.post("/api/v2/spans/events/search", json=payload) or {}
        items = data.get("data") or []
        if debug and items:
            console.rule("raw item (POST /spans/events/search)")
            console.print(RichJSON.from_data(items[0]))
        normalized = [normalize_span(it) for it in items]
        meta = {"from": from_, "to": to, "query": effective_query}
        emit(
            ctx,
            "spans.search",
            normalized,
            raw=items,
            meta=meta,
            table_renderer=lambda: _render_spans_table(items),
        )
    except Exception as exc:
        if debug:
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
            else:
                console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _error_query(service: Optional[str], extra: Optional[str], env: Optional[str] = None) -> str:
    base = _build_query(service, extra, env)
    # Generic error filter; adjust to your instrumentation
    error_filter = '(status:error OR @error.message:* OR @error.type:*)'
    if base == "*":
        return error_filter
    return f"{base} {error_filter}"


@errors_app.command(
    "top-resources",
    help=t(
        "Agrupa errores por resource_name con aggregates de spans",
        "Group errors by resource_name using spans aggregates",
    ),
)
def errors_top_resources(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help=t("Service a analizar", "Service to analyze")),
    env: Optional[str] = typer.Option(None, "--env", help=t("Filtrar por env (p.ej. prd/dev)", "Filter by env")),
    from_: str = typer.Option("now-24h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP payload/response"),
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        body = {
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {
                        "from": to_iso8601(dt_from),
                        "to": to_iso8601(dt_to),
                        "query": _error_query(service, None, env),
                    },
                    "compute": [{"aggregation": "count"}],
                    "group_by": [
                        {
                            "facet": "resource_name",
                            "limit": limit,
                            "sort": {"type": "measure", "aggregation": "count", "order": "desc"},
                        }
                    ],
                },
            }
        }
        if debug:
            console.rule("aggregate payload")
            console.print(RichJSON.from_data(body))
        with console.status("[dim]Calculando agregados de errores[/dim]"):
            data = client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
        if debug:
            console.rule("aggregate response")
            console.print(RichJSON.from_data(data))
        buckets = _extract_buckets(data)
        rows = []
        for b in buckets:
            ref = b.get("attributes") or b
            res = (ref.get("by") or {}).get("resource_name", "")
            rows.append({"resource": _trunc(res, 80), "count": _bucket_count(b)})

        def _render() -> None:
            table = new_table("Top resources by error count", {"service": service, "env": env or "", "from": from_})
            table.add_column("resource_name", style="magenta")
            table.add_column("count", style="cyan", no_wrap=True)
            for r in rows:
                table.add_row(str(r["resource"]), str(r["count"]))
            console.print(table)

        emit(
            ctx,
            "errors.top_resources",
            rows,
            raw=data,
            meta={"service": service, "env": env, "from": from_, "to": to},
            table_renderer=_render,
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@errors_app.command(
    "rate",
    help=t(
        "Cuenta spans con error agrupados por un campo (p. ej., resource_name)",
        "Count error spans grouped by a field (e.g., resource_name)",
    ),
)
def errors_rate(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help=t("Service a analizar", "Service to analyze")),
    group_by: str = typer.Option("resource_name", "--group-by", help="Facet to group by", show_default=True),
    env: Optional[str] = typer.Option(None, "--env", help=t("Filtrar por env (p.ej. prd/dev)", "Filter by env")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(10, "--limit", help="Limit", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP payload/response"),
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        body = {
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {
                        "from": to_iso8601(dt_from),
                        "to": to_iso8601(dt_to),
                        "query": _error_query(service, None, env),
                    },
                    "compute": [{"aggregation": "count"}],
                    "group_by": [
                        {
                            "facet": group_by,
                            "limit": limit,
                            "sort": {"type": "measure", "aggregation": "count", "order": "desc"},
                        }
                    ],
                },
            }
        }
        if debug:
            console.rule("aggregate payload")
            console.print(RichJSON.from_data(body))
        with console.status("[dim]Calculando agregados de errores[/dim]"):
            data = client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
        if debug:
            console.rule("aggregate response")
            console.print(RichJSON.from_data(data))
        buckets = _extract_buckets(data)
        rows = []
        for b in buckets:
            ref = b.get("attributes") or b
            key = (ref.get("by") or {}).get(group_by, "")
            rows.append({"key": _trunc(key, 80), "count": _bucket_count(b)})

        def _render() -> None:
            table = new_table(f"Error count by {group_by}", {"service": service, "env": env or "", "from": from_})
            table.add_column(group_by, style="magenta")
            table.add_column("count", style="cyan", no_wrap=True)
            for r in rows:
                table.add_row(str(r["key"]), str(r["count"]))
            console.print(table)

        emit(
            ctx,
            "errors.rate",
            rows,
            raw=data,
            meta={"service": service, "env": env, "group_by": group_by, "from": from_, "to": to},
            table_renderer=_render,
        )
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# Trace operations
# --------------------------------------------------------------------------

@trace_app.command(
    "get",
    help=t(
        "Recupera todos los spans de un trace por trace_id, ordenados por start_time",
        "Fetch all spans of a trace by trace_id, sorted by start_time",
    ),
)
def trace_get(
    ctx: typer.Context,
    trace_id: str = typer.Option(..., "--trace-id", help=t("ID del trace", "Trace ID")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(100, "--limit", help="Max spans returned", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP error details"),
) -> None:
    """
    Calls POST /api/v2/spans/events/search with `trace_id:{id}` and emits a
    flat, time-ordered list of spans suitable for trace breakdown analysis.
    """
    client = get_client_from_ctx(ctx)
    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))
        payload = {
            "data": {
                "type": "search_request",
                "attributes": {
                    "filter": {
                        "from": to_iso8601(dt_from),
                        "to": to_iso8601(dt_to),
                        "query": f"trace_id:{trace_id}",
                    },
                    "page": {"limit": limit},
                    "sort": "@start",
                },
            }
        }
        with console.status("[dim]Cargando trace[/dim]"):
            data = client.post("/api/v2/spans/events/search", json=payload) or {}
        items = data.get("data") or []
        normalized = [normalize_trace_span(it) for it in items]

        # Aggregate per (service, operation) for the table view + meta
        agg: dict = {}
        for r in normalized:
            k = (r["service"], r["operation"])
            slot = agg.setdefault(k, {"sum_ms": 0.0, "max_ms": 0.0, "n": 0})
            slot["sum_ms"] += r["dur_ms"]
            slot["max_ms"] = max(slot["max_ms"], r["dur_ms"])
            slot["n"] += 1
        total_ms = sum(r["dur_ms"] for r in normalized) if normalized else 0.0
        meta = {
            "trace_id": trace_id,
            "spans": len(normalized),
            "total_dur_ms": round(max((r["dur_ms"] for r in normalized), default=0.0), 2),
            "from": from_, "to": to,
        }

        def _render() -> None:
            tbl = new_table("Trace breakdown", {"trace_id": trace_id})
            tbl.add_column("dur_ms", style="yellow", no_wrap=True, justify="right")
            tbl.add_column("service", style="magenta", no_wrap=True)
            tbl.add_column("operation", style="cyan", no_wrap=True)
            tbl.add_column("resource", style="white")
            for r in normalized:
                tbl.add_row(f"{r['dur_ms']:,.1f}", r["service"], r["operation"], r["resource"])
            console.print(tbl)

        emit(ctx, "trace.get", normalized, raw=items, meta=meta, table_renderer=_render)
    except Exception as exc:
        if debug:
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
            else:
                console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# Generic spans aggregate
# --------------------------------------------------------------------------

@spans_app.command(
    "aggregate",
    help=t(
        "Agrega spans con percentiles arbitrarios y group_by en un facet",
        "Aggregate spans with arbitrary percentiles and group_by facet",
    ),
)
def spans_aggregate(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Filtro spans (Trace Explorer)", "Spans filter (Trace Explorer)")),
    group_by: Optional[str] = typer.Option(
        None, "--group-by",
        help=t("Facet a agrupar (p.ej. resource_name, pod_name, @http.url_details.host)",
               "Facet to group by (e.g., resource_name, pod_name, @http.url_details.host)"),
    ),
    compute: str = typer.Option(
        "count,pc50,pc95,pc99", "--compute",
        help=t("Lista coma-separada (count,pc50,pc75,pc90,pc95,pc99,avg,sum,min,max)",
               "Comma-separated list (count,pc50,pc75,pc90,pc95,pc99,avg,sum,min,max)"),
        show_default=True,
    ),
    metric: str = typer.Option(
        "@duration", "--metric",
        help=t("Métrica numérica para los percentiles", "Numeric metric for percentiles"),
        show_default=True,
    ),
    sort_by: str = typer.Option(
        "pc95", "--sort-by",
        help=t("Aggregation usado para ordenar buckets", "Aggregation used to sort buckets"),
        show_default=True,
    ),
    order: str = typer.Option("desc", "--order", help="asc|desc", show_default=True),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    limit: int = typer.Option(15, "--limit", help="Max buckets", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP payload/response"),
) -> None:
    """
    Generic wrapper around POST /api/v2/spans/analytics/aggregate that
    accepts arbitrary computes (count + multiple percentiles) and an optional
    group_by facet. Returns one row per bucket with `key, count, p50_ms,
    p95_ms, p99_ms` (or whichever percentiles you requested).
    """
    client = get_client_from_ctx(ctx)
    aggs = [a.strip() for a in compute.split(",") if a.strip()]
    if not aggs:
        raise typer.BadParameter("--compute must list at least one aggregation")

    def _to_compute_entry(name: str) -> dict:
        if name == "count":
            return {"aggregation": "count"}
        return {"aggregation": name, "metric": metric}

    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))

        body_attrs = {
            "filter": {
                "from": to_iso8601(dt_from),
                "to": to_iso8601(dt_to),
                "query": query,
            },
            "compute": [_to_compute_entry(a) for a in aggs],
        }
        if group_by:
            sort_entry = {"type": "measure", "aggregation": sort_by, "order": order}
            if sort_by != "count":
                sort_entry["metric"] = metric
            body_attrs["group_by"] = [{
                "facet": group_by,
                "limit": limit,
                "sort": sort_entry,
            }]
        body = {"data": {"type": "aggregate_request", "attributes": body_attrs}}
        if debug:
            console.rule("aggregate payload")
            console.print(RichJSON.from_data(body))
        with console.status("[dim]Calculando agregados[/dim]"):
            data = client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
        if debug:
            console.rule("aggregate response")
            console.print(RichJSON.from_data(data))
        buckets = _norm_extract_buckets(data)

        def _ms(v) -> float:
            try:
                return round(float(v or 0) / 1e6, 2)
            except Exception:
                return 0.0

        rows: List[dict] = []
        for b in buckets:
            a = b.get("attributes", {}) or b
            c = a.get("compute") or {}
            row: dict = {
                "key": _trunc((a.get("by") or {}).get(group_by, "") if group_by else "*", 80),
            }
            for idx, agg in enumerate(aggs):
                col = f"c{idx}"
                v = c.get(col)
                if agg == "count":
                    row["count"] = int(v or 0)
                elif agg in ("pc50", "pc75", "pc90", "pc95", "pc99"):
                    row[f"p{agg[2:]}_ms"] = _ms(v)
                else:
                    row[agg] = v
            rows.append(row)

        def _render() -> None:
            cols = list(rows[0].keys()) if rows else ["key"]
            tbl = new_table("Spans aggregate", {"from": from_, "to": to})
            for col in cols:
                tbl.add_column(col, style="cyan" if col == "key" else "yellow", no_wrap=True)
            for r in rows:
                tbl.add_row(*[str(r.get(c, "")) for c in cols])
            console.print(tbl)

        meta = {
            "query": query, "group_by": group_by, "compute": aggs,
            "metric": metric, "sort_by": sort_by, "order": order,
            "from": from_, "to": to,
        }
        emit(ctx, "spans.aggregate", rows, raw=data, meta=meta, table_renderer=_render)
    except Exception as exc:
        if debug:
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
            else:
                console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# Trace timeseries
# --------------------------------------------------------------------------

@trace_app.command(
    "timeseries",
    help=t(
        "Time-series de count + percentiles para un filtro de spans",
        "Time-series of count + percentiles for a spans filter",
    ),
)
def trace_timeseries(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Filtro spans (Trace Explorer)", "Spans filter (Trace Explorer)")),
    interval: str = typer.Option("1h", "--interval", help=t("Intervalo (p.ej. 5m, 1h)", "Interval (e.g., 5m, 1h)"), show_default=True),
    compute: str = typer.Option(
        "count,pc95,pc99", "--compute",
        help=t("Lista coma-separada de aggregations", "Comma-separated aggregations"),
        show_default=True,
    ),
    metric: str = typer.Option("@duration", "--metric", help="Numeric metric", show_default=True),
    from_: str = typer.Option("now-24h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP payload/response"),
) -> None:
    """
    Calls POST /api/v2/spans/analytics/aggregate with an `interval` to get
    time-bucketed counts + percentiles. Useful for spotting deploy-time
    regressions or load-correlated latency spikes.
    """
    client = get_client_from_ctx(ctx)
    aggs = [a.strip() for a in compute.split(",") if a.strip()]

    def _to_compute_entry(name: str) -> dict:
        if name == "count":
            return {"aggregation": "count"}
        return {"aggregation": name, "metric": metric}

    try:
        dt_from = parse_time(from_)
        dt_to = parse_time(to)
        if dt_to < dt_from:
            raise typer.BadParameter(t("--to debe ser >= --from", "--to must be >= --from"))

        # Datadog Spans Analytics has no native time-series mode, so we slice
        # the [from, to] window into N buckets of `interval` width and issue
        # one aggregate_request per bucket. N is bounded by --max-buckets.
        from datetime import timedelta
        unit_secs = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        try:
            n, u = int(interval[:-1]), interval[-1]
            bucket_secs = n * unit_secs[u]
        except Exception:
            raise typer.BadParameter(f"--interval must look like 5m, 1h, 4h, 1d (got {interval!r})")
        total = (dt_to - dt_from).total_seconds()
        n_buckets = int(total // bucket_secs) + (1 if total % bucket_secs else 0)
        if n_buckets > 24:
            sys.stderr.write(
                f"warning: {n_buckets} buckets — capping to 24. Increase --interval to widen.\n"
            )
            sys.stderr.flush()
            n_buckets = 24

        rows: List[dict] = []
        raw_resps: List[dict] = []
        # Avoid console.status(...) here: the Rich Live display does not play
        # well with stdout redirection for multi-call commands and can swallow
        # the final JSON write. A plain stderr line is enough.
        if not (ctx.obj or {}).get("json"):
            sys.stderr.write(f"Time-series ({n_buckets} buckets)…\n")
            sys.stderr.flush()

        # Datadog spans aggregate occasionally returns transient 5xx; one bad
        # bucket would otherwise abort the whole timeseries. Retry per bucket
        # with exponential backoff (1s, 2s, 4s) on retriable errors.
        import time as _time
        def _post_with_retry(body: dict, max_retries: int = 3) -> dict:
            backoff = 1.0
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
                except ApiError as e:
                    last_exc = e
                    if e.status_code is None or e.status_code < 500 or attempt >= max_retries:
                        raise
                    sys.stderr.write(
                        f"trace_timeseries: HTTP {e.status_code} on bucket, "
                        f"retry {attempt + 1}/{max_retries} in {backoff:.1f}s\n"
                    )
                    sys.stderr.flush()
                    _time.sleep(backoff)
                    backoff *= 2
            assert last_exc is not None
            raise last_exc

        for i in range(n_buckets):
            b_from = dt_from + timedelta(seconds=i * bucket_secs)
            b_to = b_from + timedelta(seconds=bucket_secs)
            if b_to > dt_to:
                b_to = dt_to
            body = {"data": {"type": "aggregate_request", "attributes": {
                "filter": {"from": to_iso8601(b_from), "to": to_iso8601(b_to), "query": query},
                "compute": [_to_compute_entry(a) for a in aggs],
            }}}
            if debug and i == 0:
                console.rule("timeseries payload (bucket 0)")
                console.print(RichJSON.from_data(body))

            row = {"ts": _ts_to_iso(b_from.isoformat())}
            try:
                resp = _post_with_retry(body)
            except ApiError as e:
                # Soft-fail: tag the bucket as errored and continue. A single
                # bad bucket should not abort an otherwise-successful 24-bucket
                # timeseries — the user can re-run the command to fill it.
                sys.stderr.write(
                    f"trace_timeseries: bucket {row['ts']} failed after retries "
                    f"(HTTP {e.status_code}); leaving null and continuing.\n"
                )
                sys.stderr.flush()
                resp = {}
                row["error"] = f"HTTP {e.status_code}"
            raw_resps.append({"from": to_iso8601(b_from), "to": to_iso8601(b_to), "resp": resp})
            buckets = _norm_extract_buckets(resp)
            c = (((buckets[0] or {}).get("attributes") or buckets[0] or {}).get("compute") or {}) if buckets else {}

            def _ms(v) -> float:
                try: return round(float(v or 0) / 1e6, 2)
                except Exception: return 0.0

            for idx, agg in enumerate(aggs):
                v = c.get(f"c{idx}")
                if agg == "count":
                    try: row["count"] = int(v or 0)
                    except Exception: row["count"] = 0
                elif agg in ("pc50", "pc75", "pc90", "pc95", "pc99"):
                    row[f"p{agg[2:]}_ms"] = _ms(v)
                else:
                    row[agg] = v
            rows.append(row)
        data = {"buckets": raw_resps}

        def _render() -> None:
            if not rows: console.print("[yellow]no data[/yellow]"); return
            cols = list(rows[0].keys())
            tbl = new_table("Trace timeseries", {"from": from_, "to": to})
            for col in cols:
                tbl.add_column(col, style="cyan" if col == "ts" else "yellow", no_wrap=True)
            for r in rows:
                tbl.add_row(*[str(r.get(c, "")) for c in cols])
            console.print(tbl)

        meta = {"query": query, "interval": interval, "compute": aggs,
                "metric": metric, "from": from_, "to": to}
        emit(ctx, "trace.timeseries", rows, raw=data, meta=meta, table_renderer=_render)
    except Exception as exc:
        # ALWAYS log the exception to stderr — silently swallowing it makes
        # downstream parsers see exit=1 with no diagnostic, which is worse
        # than a noisy stderr line.
        import traceback as _tb
        sys.stderr.write(f"trace_timeseries error: {type(exc).__name__}: {exc}\n")
        if debug:
            _tb.print_exc(file=sys.stderr)
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc

