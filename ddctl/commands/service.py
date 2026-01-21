from __future__ import annotations

from typing import Optional, Tuple, List

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.json import JSON as RichJSON

from ..cli import get_client_from_ctx
from ..api import ApiError, ApiClient
from ..utils_time import parse_time, to_iso8601
from ..i18n import t
from ..options import DebugOption
from ..ui import new_table, build_title

# Reuse helpers from APM module
from .apm import _build_query as _apm_build_query
from .apm import _error_query as _apm_error_query
from .apm import _extract_buckets as _apm_extract_buckets

app = typer.Typer(help=t("Diagnóstico unificado por servicio", "Unified service troubleshooting"))
console = Console()


def _cluster_extra(cluster: Optional[str]) -> Optional[str]:
    if not cluster:
        return None
    return f"cluster:{cluster}"


def _safe_get_compute_values(resp: dict) -> dict:
    """
    Tries to extract compute results from aggregate responses.
    Supports both 'compute': {'c0': X, 'c1': Y} and 'computes': [{'value': X}, {'value': Y}] shapes,
    optionally wrapped in a single bucket.
    Returns a map like {'c0': <num>, 'c1': <num>} when possible.
    """
    if not isinstance(resp, dict):
        return {}
    # First, try buckets (common response shape even without group_by)
    buckets = _apm_extract_buckets(resp)
    if isinstance(buckets, list) and buckets:
        # Take the first bucket for totals
        ref = (buckets[0] or {}).get("attributes") or buckets[0] or {}
        compute = ref.get("compute") or {}
        if isinstance(compute, dict) and ("c0" in compute or "c1" in compute or "c2" in compute):
            return compute
        computes = ref.get("computes")
        if isinstance(computes, list) and computes:
            out = {}
            for idx, entry in enumerate(computes):
                if isinstance(entry, dict) and "value" in entry:
                    out[f"c{idx}"] = entry.get("value")
            if out:
                return out
    # Fallback: some APIs may place totals directly under attributes
    attrs = (resp.get("data") or {}).get("attributes") if isinstance(resp.get("data"), dict) else resp.get("attributes") or {}
    if isinstance(attrs, dict):
        compute = attrs.get("compute") or {}
        if isinstance(compute, dict) and compute:
            return compute
        computes = attrs.get("computes")
        if isinstance(computes, list) and computes:
            out = {}
            for idx, entry in enumerate(computes):
                if isinstance(entry, dict) and "value" in entry:
                    out[f"c{idx}"] = entry.get("value")
            if out:
                return out
    return {}


def _convert_duration_to_ms(value: float) -> float:
    """
    Heuristic conversion:
    - If it looks like nanoseconds (very large), convert ns -> ms
    - Else if it looks like microseconds, convert us -> ms
    - Else assume already ms or seconds-ish; if < 10, assume seconds and convert to ms.
    """
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v > 10_000_000:  # likely nanoseconds
        return v / 1_000_000.0
    if v > 10_000:  # likely microseconds
        return v / 1000.0
    # If small number, assume seconds -> ms
    if v <= 10:
        return v * 1000.0
    return v  # assume already ms


def _render_overview_table(total_count: int, error_count: int, p95_ms: float, from_label: str, service: str, env: Optional[str], cluster: Optional[str]) -> None:
    table = new_table(
        "APM overview",
        {"service": service, "env": env or "", "cluster": cluster or "", "from": from_label},
    )
    table.add_column("metric", style="cyan", no_wrap=True)
    table.add_column("value", style="magenta", no_wrap=True)

    err_rate = (error_count / max(1, total_count)) if total_count else 0.0
    table.add_row("total_spans", str(total_count))
    table.add_row("error_spans", str(error_count))
    table.add_row("error_rate", f"{err_rate*100:.2f}%")
    table.add_row("p95_latency_ms", f"{p95_ms:.2f}")
    console.print(table)


def _render_top_errors_table(buckets: List[dict], service: str, env: Optional[str], from_label: str, cluster: Optional[str]) -> None:
    table = new_table("Top error resources (resource_name)", {"service": service, "env": env or "", "cluster": cluster or "", "from": from_label})
    table.add_column("resource_name", style="magenta")
    table.add_column("count", style="cyan", no_wrap=True)
    for b in buckets:
        ref = b.get("attributes") or b
        res = (ref.get("by") or {}).get("resource_name", "")
        compute_obj = ref.get("compute") or {}
        if isinstance(compute_obj, dict) and "c0" in compute_obj:
            count = compute_obj.get("c0", 0)
        else:
            computes = ref.get("computes") or [{}]
            count = (computes[0] or {}).get("value", 0)
        table.add_row(str(res), str(count))
    console.print(table)


def _render_logs_table(items: List[dict], service: str, env: Optional[str], from_label: str, cluster: Optional[str]) -> None:
    if not items:
        console.print(
            Panel.fit(
                t("No hay datos de logs en el rango seleccionado.", "No logs data in the selected range."),
                title=build_title("Logs", {"service": service, "env": env or "", "cluster": cluster or "", "from": from_label}),
                border_style="yellow",
            )
        )
        return
    table = new_table("Last error logs", {"service": service, "env": env or "", "cluster": cluster or "", "from": from_label})
    table.add_column("timestamp", style="cyan", no_wrap=True)
    table.add_column("service", style="magenta", no_wrap=True)
    table.add_column("status", style="green", no_wrap=True)
    table.add_column("message", style="white")
    for item in items:
        attrs = (item or {}).get("attributes") or {}
        timestamp = attrs.get("timestamp", "") or ""
        nested_attrs = attrs.get("attributes") or {}
        service_val = nested_attrs.get("service") or attrs.get("service") or ""
        status_val = attrs.get("status") or ""
        message_val = nested_attrs.get("message") or attrs.get("message") or ""
        if isinstance(message_val, (dict, list)):
            message_val = str(message_val)
        msg = (str(message_val) or "")[:400]
        table.add_row(str(timestamp), str(service_val), str(status_val), msg)
    console.print(table)


def _heuristic_summary(error_rate: float, p95_ms: float, top_resources: List[Tuple[str, int]]) -> str:
    bullets: List[str] = []
    if error_rate >= 0.05:
        bullets.append(f"[red]- Alta tasa de errores[/red] ({error_rate*100:.2f}%)")
    else:
        bullets.append(f"[green]- Tasa de errores baja[/green] ({error_rate*100:.2f}%)")
    if p95_ms >= 500:
        bullets.append(f"[yellow]- Latencia p95 elevada[/yellow] ({p95_ms:.0f} ms)")
    else:
        bullets.append(f"[green]- Latencia p95 ok[/green] ({p95_ms:.0f} ms)")
    if top_resources:
        head = ", ".join([f"{name} ({count})" for name, count in top_resources[:3]])
        bullets.append(f"- Principales recursos con error: {head}")
    return "\n".join(bullets) or "-"


@app.command("troubleshoot", help=t("Vista de diagnóstico APM+Logs para un servicio", "Unified APM+Logs troubleshooting view"))
def service_troubleshoot(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help=t("Nombre del servicio", "Service name")),
    env: Optional[str] = typer.Option(None, "--env", help=t("Entorno (p. ej., prd/dev)", "Environment (e.g., prd/dev)")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    cluster: Optional[str] = typer.Option(None, "--cluster", help=t("Filtrar por cluster:<name>", "Filter by cluster:<name>")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)

    try:
        dt_from = parse_time(from_)
        # Always to now
        payload_from = to_iso8601(dt_from)
        payload_to = to_iso8601(parse_time("now"))
        extra = _cluster_extra(cluster)

        # APM overview: totals + p95
        body_overview = {
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {
                        "from": payload_from,
                        "to": payload_to,
                        "query": _apm_build_query(service, extra, env),
                    },
                    "compute": [
                        {"aggregation": "count"},  # c0
                        {"aggregation": "pc95", "metric": "duration"},  # c1
                    ],
                },
            }
        }
        if debug:
            console.rule("APM overview payload")
            console.print(RichJSON.from_data(body_overview))
        with console.status("[dim]APM overview[/dim]"):
            resp_overview = client.post("/api/v2/spans/analytics/aggregate", json=body_overview) or {}
        if debug:
            console.rule("APM overview response")
            console.print(RichJSON.from_data(resp_overview))
        computes_ov = _safe_get_compute_values(resp_overview)
        total_count = int(computes_ov.get("c0") or 0)
        p95_raw = float(computes_ov.get("c1") or 0.0)
        p95_ms = _convert_duration_to_ms(p95_raw)

        # APM errors: count
        body_errors = {
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {
                        "from": payload_from,
                        "to": payload_to,
                        "query": _apm_error_query(service, extra, env),
                    },
                    "compute": [
                        {"aggregation": "count"},  # c0
                    ],
                },
            }
        }
        if debug:
            console.rule("APM errors payload")
            console.print(RichJSON.from_data(body_errors))
        with console.status("[dim]APM errores[/dim]"):
            resp_errors = client.post("/api/v2/spans/analytics/aggregate", json=body_errors) or {}
        if debug:
            console.rule("APM errors response")
            console.print(RichJSON.from_data(resp_errors))
        computes_err = _safe_get_compute_values(resp_errors)
        error_count = int(computes_err.get("c0") or 0)

        # APM top error resources
        body_top = {
            "data": {
                "type": "aggregate_request",
                "attributes": {
                    "filter": {
                        "from": payload_from,
                        "to": payload_to,
                        "query": _apm_error_query(service, extra, env),
                    },
                    "compute": [{"aggregation": "count"}],
                    "group_by": [
                        {
                            "facet": "resource_name",
                            "limit": 10,
                            "sort": {"type": "measure", "aggregation": "count", "order": "desc"},
                        }
                    ],
                },
            }
        }
        if debug:
            console.rule("APM top-resources payload")
            console.print(RichJSON.from_data(body_top))
        with console.status("[dim]APM top recursos[/dim]"):
            resp_top = client.post("/api/v2/spans/analytics/aggregate", json=body_top) or {}
        if debug:
            console.rule("APM top-resources response")
            console.print(RichJSON.from_data(resp_top))
        buckets_top = _apm_extract_buckets(resp_top)

        # Logs: last 10 error logs
        q_parts = [f"service:{service}", "status:error"]
        if env:
            q_parts.append(f"env:{env}")
        if cluster:
            q_parts.append(f"cluster:{cluster}")
        logs_payload = {
            "filter": {
                "from": payload_from,
                "to": payload_to,
                "query": " ".join(q_parts),
            },
            "page": {"limit": 10},
            "sort": "-timestamp",
        }
        if debug:
            console.rule("Logs search payload")
            console.print(RichJSON.from_data(logs_payload))
        with console.status("[dim]Consultando logs[/dim]"):
            logs_resp = client.post("/api/v2/logs/events/search", json=logs_payload) or {}
        if debug:
            console.rule("Logs search response")
            console.print(RichJSON.from_data(logs_resp))
        log_items = (logs_resp or {}).get("data") or []

        # Render
        _render_overview_table(total_count, error_count, p95_ms, from_, service, env, cluster)
        _render_top_errors_table(buckets_top or [], service, env, from_, cluster)
        _render_logs_table(log_items, service, env, from_, cluster)

        # Heuristic summary
        err_rate = (error_count / max(1, total_count)) if total_count else 0.0
        top_pairs: List[Tuple[str, int]] = []
        for b in buckets_top or []:
            ref = b.get("attributes") or b
            res = (ref.get("by") or {}).get("resource_name", "")
            compute_obj = ref.get("compute") or {}
            if isinstance(compute_obj, dict) and "c0" in compute_obj:
                cnt = int(compute_obj.get("c0") or 0)
            else:
                computes = ref.get("computes") or [{}]
                cnt = int((computes[0] or {}).get("value") or 0)
            if res:
                top_pairs.append((str(res), cnt))
        summary_text = _heuristic_summary(err_rate, p95_ms, top_pairs)
        console.print(Panel.fit(summary_text, title="Resumen", border_style="blue"))

    except Exception as exc:
        if debug:
            if isinstance(exc, ApiError):
                console.print(f"[red]HTTP {exc.status_code}[/red]")
                try:
                    console.print(RichJSON.from_data(exc.payload))
                except Exception:
                    console.print(str(exc.payload))
            else:
                console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

