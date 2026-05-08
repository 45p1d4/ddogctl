from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.json import JSON as RichJSON

from ..cli import get_client_from_ctx
from ..api import ApiError
from ..i18n import t
from ..options import DebugOption
from ..normalize import normalize_host
from ..ui import emit, new_table

app = typer.Typer(help=t("Operaciones sobre Hosts (Infrastructure)", "Hosts (Infrastructure) operations"))
console = Console()


@app.command(
    "list",
    help=t(
        "GET /api/v1/hosts — listar hosts con filtros opcionales",
        "GET /api/v1/hosts — list hosts with optional filters",
    ),
)
def list_hosts(
    ctx: typer.Context,
    filter_: Optional[str] = typer.Option(
        None, "--filter",
        help=t("Filtro de hosts (p.ej. env:prd OR cluster:tor)", "Hosts filter (e.g., env:prd OR cluster:tor)"),
    ),
    count: int = typer.Option(20, "--count", help=t("Cantidad máxima", "Max count"), show_default=True),
    sort_field: str = typer.Option("status", "--sort-field", help="Sort field"),
    sort_dir: str = typer.Option("desc", "--sort-dir", help="Sort direction"),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        params = {"count": count, "sort_field": sort_field, "sort_dir": sort_dir}
        if filter_:
            params["filter"] = filter_
        with console.status("[dim]Cargando hosts[/dim]"):
            resp = client.get("/api/v1/hosts", params=params) or {}
        items = resp.get("host_list") or resp.get("hosts") or []
        normalized = [normalize_host(it) for it in items]

        def _render() -> None:
            if debug:
                console.print(RichJSON.from_data(resp))
                return
            table = new_table("Hosts")
            table.add_column("name", style="cyan", no_wrap=True)
            table.add_column("up", style="green", no_wrap=True)
            table.add_column("muted", style="yellow", no_wrap=True)
            table.add_column("apps", style="white")
            table.add_column("tags_relevant", style="magenta")
            for r in normalized:
                table.add_row(
                    str(r.get("name") or ""),
                    str(r.get("up") or ""),
                    str(r.get("muted") or ""),
                    ", ".join(r.get("apps") or []),
                    ", ".join(r.get("tags_relevant") or []),
                )
            console.print(table)

        emit(
            ctx,
            "hosts.list",
            normalized,
            meta={"filter": filter_, "count": count, "total_returned": resp.get("total_returned")},
            table_renderer=_render,
        )
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "count",
    help=t(
        "GET /api/v1/hosts/totals — totales (up/active)",
        "GET /api/v1/hosts/totals — totals (up/active)",
    ),
)
def count_hosts(
    ctx: typer.Context,
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Contando hosts[/dim]"):
            resp = client.get("/api/v1/hosts/totals") or {}
        result = {
            "total_up": resp.get("total_up"),
            "total_active": resp.get("total_active"),
            "total": resp.get("total_active"),
        }

        def _render() -> None:
            console.print(RichJSON.from_data(resp))

        emit(ctx, "hosts.count", result, table_renderer=_render)
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "mute",
    help=t(
        "POST /api/v1/host/{name}/mute — silenciar un host",
        "POST /api/v1/host/{name}/mute — mute a host",
    ),
)
def mute_host(
    ctx: typer.Context,
    host: str = typer.Option(..., "--host", help=t("Nombre del host", "Host name")),
    message: Optional[str] = typer.Option(None, "--message", help=t("Razón del mute", "Mute reason")),
    end: Optional[int] = typer.Option(None, "--end", help=t("Epoch segundos en que termina el mute", "Mute end (epoch seconds)")),
    override: bool = typer.Option(False, "--override", help=t("Sobreescribir mute existente", "Override existing mute")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        body = {"override": override}
        if message:
            body["message"] = message
        if end:
            body["end"] = end
        with console.status("[dim]Silenciando host[/dim]"):
            resp = client.post(f"/api/v1/host/{host}/mute", json=body) or {}
        result = {"host": host, "muted": True, "end": resp.get("end")}
        emit(ctx, "hosts.mute", result, table_renderer=lambda: console.print(RichJSON.from_data(resp)))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc


@app.command(
    "unmute",
    help=t(
        "POST /api/v1/host/{name}/unmute — quitar mute",
        "POST /api/v1/host/{name}/unmute — remove mute",
    ),
)
def unmute_host(
    ctx: typer.Context,
    host: str = typer.Option(..., "--host", help=t("Nombre del host", "Host name")),
    debug: DebugOption = False,
) -> None:
    client = get_client_from_ctx(ctx)
    try:
        with console.status("[dim]Quitando mute[/dim]"):
            resp = client.post(f"/api/v1/host/{host}/unmute", json={}) or {}
        result = {"host": host, "muted": False}
        emit(ctx, "hosts.unmute", result, table_renderer=lambda: console.print(RichJSON.from_data(resp)))
    except Exception as exc:
        if debug and isinstance(exc, ApiError):
            console.print(f"[red]HTTP {exc.status_code}[/red] {exc.payload}")
        raise typer.Exit(code=1) from exc
