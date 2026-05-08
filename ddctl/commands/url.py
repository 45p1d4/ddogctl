"""
URL composition for the Datadog UI. Pure helpers — no API calls.

These commands turn a trace_id, query, service name or monitor id into a
deep link to the Datadog UI, with the time range encoded as 13-digit
millisecond epoch (the format the UI expects). Useful for handing off
evidence to teammates without copy-pasting a manually built URL.
"""
from __future__ import annotations

import urllib.parse as up
from typing import Optional

import typer
from rich.console import Console

from ..i18n import t
from ..ui import emit
from ..utils_time import parse_time

app = typer.Typer(help=t("Generadores de links a la UI de Datadog (sin llamadas API)",
                          "Datadog UI link generators (no API calls)"))
console = Console()

DEFAULT_BASE = "https://app.datadoghq.com"


def _ms(expr: str) -> int:
    """Parse a relative/ISO time expression into 13-digit ms epoch."""
    return int(parse_time(expr).timestamp() * 1000)


def _site_to_base(site: Optional[str]) -> str:
    """`datadoghq.com` -> https://app.datadoghq.com  (and EU/US3 etc.)."""
    if not site:
        return DEFAULT_BASE
    return f"https://app.{site}"


def _base(ctx: typer.Context, override: Optional[str]) -> str:
    if override:
        return override
    # Try to resolve from the context name -> YAML site (no API call needed).
    try:
        from ..config import resolve_context
        site, _, _ = resolve_context(
            (ctx.obj or {}).get("context_name"),
            (ctx.obj or {}).get("config_path"),
        )
        return _site_to_base(site)
    except Exception:
        return DEFAULT_BASE


# --------------------------------------------------------------------------

@app.command(
    "trace",
    help=t("URL del trace viewer para un trace_id", "Trace viewer URL for a trace_id"),
)
def trace_url(
    ctx: typer.Context,
    trace_id: str = typer.Option(..., "--trace-id", help=t("ID del trace", "Trace ID")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    base: Optional[str] = typer.Option(None, "--base", help=t("Override del base URL DD", "Override DD base URL")),
) -> None:
    b = _base(ctx, base)
    start_ms, end_ms = _ms(from_), _ms(to)
    url = f"{b}/apm/trace/{trace_id}?start={start_ms}&end={end_ms}"
    emit(
        ctx, "url.trace",
        {"url": url, "trace_id": trace_id, "from": from_, "to": to, "start_ms": start_ms, "end_ms": end_ms},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "explorer",
    help=t("URL del Trace Explorer para una query", "Trace Explorer URL for a query"),
)
def explorer_url(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Filtro de spans", "Spans filter")),
    from_: str = typer.Option("now-24h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    start_ms, end_ms = _ms(from_), _ms(to)
    qs = up.urlencode({"query": query, "start": start_ms, "end": end_ms})
    url = f"{b}/apm/traces?{qs}"
    emit(
        ctx, "url.explorer",
        {"url": url, "query": query, "from": from_, "to": to, "start_ms": start_ms, "end_ms": end_ms},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "service",
    help=t("URL de la página de servicio APM", "APM service page URL"),
)
def service_url(
    ctx: typer.Context,
    service: str = typer.Option(..., "--service", help=t("Nombre del servicio", "Service name")),
    env: str = typer.Option("prd", "--env", help=t("Entorno", "Environment"), show_default=True),
    from_: str = typer.Option("now-24h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    start_ms, end_ms = _ms(from_), _ms(to)
    url = f"{b}/apm/services/{service}?env={env}&start={start_ms}&end={end_ms}"
    emit(
        ctx, "url.service",
        {"url": url, "service": service, "env": env, "from": from_, "to": to,
         "start_ms": start_ms, "end_ms": end_ms},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "logs",
    help=t("URL del Logs Explorer para una query", "Logs Explorer URL for a query"),
)
def logs_url(
    ctx: typer.Context,
    query: str = typer.Option(..., "--query", help=t("Filtro de logs", "Logs filter")),
    from_: str = typer.Option("now-1h", "--from", help=t("Inicio del rango", "Range start"), show_default=True),
    to: str = typer.Option("now", "--to", help=t("Fin del rango", "Range end"), show_default=True),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    start_ms, end_ms = _ms(from_), _ms(to)
    qs = up.urlencode({"query": query, "from_ts": start_ms, "to_ts": end_ms, "live": "false"})
    url = f"{b}/logs?{qs}"
    emit(
        ctx, "url.logs",
        {"url": url, "query": query, "from": from_, "to": to, "start_ms": start_ms, "end_ms": end_ms},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "monitor",
    help=t("URL del monitor por ID", "Monitor URL by ID"),
)
def monitor_url(
    ctx: typer.Context,
    id: int = typer.Option(..., "--id", help=t("ID del monitor", "Monitor ID")),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    url = f"{b}/monitors/{id}"
    emit(
        ctx, "url.monitor",
        {"url": url, "id": id},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "dashboard",
    help=t("URL del dashboard por ID", "Dashboard URL by ID"),
)
def dashboard_url(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help=t("ID del dashboard", "Dashboard ID")),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    url = f"{b}/dashboard/{id}"
    emit(
        ctx, "url.dashboard",
        {"url": url, "id": id},
        table_renderer=lambda: console.print(url),
    )


@app.command(
    "incident",
    help=t("URL del incidente por ID", "Incident URL by ID"),
)
def incident_url(
    ctx: typer.Context,
    id: str = typer.Option(..., "--id", help=t("ID del incidente", "Incident ID")),
    base: Optional[str] = typer.Option(None, "--base"),
) -> None:
    b = _base(ctx, base)
    url = f"{b}/incidents/{id}"
    emit(
        ctx, "url.incident",
        {"url": url, "id": id},
        table_renderer=lambda: console.print(url),
    )
