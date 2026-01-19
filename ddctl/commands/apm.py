from __future__ import annotations

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

app = typer.Typer(help=t("Operaciones de APM", "APM operations"))
console = Console()

spans_app = typer.Typer(help=t("Operaciones sobre Spans", "Spans operations"))
errors_app = typer.Typer(help=t("Reportes de errores", "Error analytics"))
app.add_typer(spans_app, name="spans")
app.add_typer(errors_app, name="errors")


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

    # Title composition with common env/service
    title_parts = ["Spans"]
    if len(date_set) == 1:
        title_parts.append(f"date={next(iter(date_set))}")
    if len(env_set) == 1:
        title_parts.append(f"env={next(iter(env_set))}")
    if len(service_set) == 1:
        title_parts.append(f"service={next(iter(service_set))}")
    title = " (" + ", ".join(title_parts[1:]) + ")" if len(title_parts) > 1 else "Spans"

    table = Table(title="Spans" + ("" if len(title_parts) == 1 else " " + title), show_lines=False)
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
    limit: int = typer.Option(50, "--limit", help="Limit", show_default=True),
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
        data = client.get("/api/v2/spans/events", params=params) or {}
        items = data.get("data") or []
        if debug and items:
            console.rule("raw item (GET /spans/events)")
            console.print(RichJSON.from_data(items[0]))
        _render_spans_table(items)
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
    limit: int = typer.Option(50, "--limit", help="Limit", show_default=True),
    sort: str = typer.Option("-timestamp", "--sort", help="Sort", show_default=True),
    debug: bool = typer.Option(False, "--debug", help="Show HTTP error details"),
) -> None:
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
                        "query": _build_query(None, query, env) if env else (query or "*"),
                    },
                    "page": {"limit": limit},
                    "sort": sort,
                },
            }
        }
        data = client.post("/api/v2/spans/events/search", json=payload) or {}
        items = data.get("data") or []
        if debug and items:
            console.rule("raw item (POST /spans/events/search)")
            console.print(RichJSON.from_data(items[0]))
        _render_spans_table(items)
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
        data = client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
        if debug:
            console.rule("aggregate response")
            console.print(RichJSON.from_data(data))
        buckets = _extract_buckets(data)
        table = Table(title="Top resources by error count", show_lines=False)
        table.add_column("resource_name", style="magenta")
        table.add_column("count", style="cyan", no_wrap=True)
        for b in buckets:
            ref = b.get("attributes") or b
            res = (ref.get("by") or {}).get("resource_name", "")
            # Datadog returns either 'compute': {'c0': N} or 'computes': [{'value': N}]
            compute_obj = ref.get("compute") or {}
            if isinstance(compute_obj, dict) and "c0" in compute_obj:
                count = compute_obj.get("c0", 0)
            else:
                computes = ref.get("computes") or [{}]
                count = (computes[0] or {}).get("value", 0)
            table.add_row(str(res), str(count))
        console.print(table)
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
        data = client.post("/api/v2/spans/analytics/aggregate", json=body) or {}
        if debug:
            console.rule("aggregate response")
            console.print(RichJSON.from_data(data))
        buckets = _extract_buckets(data)
        table = Table(title=f"Error count by {group_by}", show_lines=False)
        table.add_column(group_by, style="magenta")
        table.add_column("count", style="cyan", no_wrap=True)
        for b in buckets:
            ref = b.get("attributes") or b
            key = (ref.get("by") or {}).get(group_by, "")
            compute_obj = ref.get("compute") or {}
            if isinstance(compute_obj, dict) and "c0" in compute_obj:
                count = compute_obj.get("c0", 0)
            else:
                computes = ref.get("computes") or [{}]
                count = (computes[0] or {}).get("value", 0)
            table.add_row(str(key), str(count))
        console.print(table)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

