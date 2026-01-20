from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .api import ApiClient
from .i18n import t

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=t("CLI de Datadog (ddogctl)", "Datadog CLI (ddogctl)"),
)


def _ensure_ctx(ctx: typer.Context) -> None:
    if ctx.obj is None:
        ctx.obj = {}


@app.callback(invoke_without_command=False)
def main(
    ctx: typer.Context,
    context: Optional[str] = typer.Option(
        None, "--context", help=t("Nombre del contexto a usar desde el YAML", "Context name to use from YAML")
        ),
    config: Optional[Path] = typer.Option(
        None, "--config", help=t("Ruta al archivo de configuración YAML", "Path to YAML config file")
        ),
) -> None:
    # Global options only stored in context
    _ensure_ctx(ctx)
    ctx.obj["context_name"] = context
    ctx.obj["config_path"] = str(config) if config else None


def get_client_from_ctx(ctx: typer.Context) -> ApiClient:
    _ensure_ctx(ctx)
    context_name = ctx.obj.get("context_name")
    config_path = ctx.obj.get("config_path")
    # No caching para simplicidad y evitar inconsistencias con cambios de opciones
    client = ApiClient.create_from_context(context_name, config_path)
    return client


# Registrar sub-apps
from .commands import auth as auth_cmd  # noqa: E402
from .commands import monitors as monitors_cmd  # noqa: E402
from .commands import dashboards as dashboards_cmd  # noqa: E402
from .commands import incidents as incidents_cmd  # noqa: E402
from .commands import synthetics as synthetics_cmd  # noqa: E402
from .commands import logs as logs_cmd  # noqa: E402
from .commands import apm as apm_cmd  # noqa: E402
from .commands import services as services_cmd  # noqa: E402
from .commands import metrics as metrics_cmd  # noqa: E402
from .i18n import t
from rich.console import Console
from importlib import resources as importlib_resources

app.add_typer(auth_cmd.app, name="auth")
app.add_typer(monitors_cmd.app, name="monitors")
app.add_typer(dashboards_cmd.app, name="dashboards")
app.add_typer(incidents_cmd.app, name="incidents")
app.add_typer(synthetics_cmd.app, name="synthetics")
app.add_typer(logs_cmd.app, name="logs")
app.add_typer(apm_cmd.app, name="apm")
app.add_typer(services_cmd.app, name="services")
app.add_typer(metrics_cmd.app, name="metrics")

_console = Console()

def _load_banner() -> str:
    try:
        return importlib_resources.files("ddctl").joinpath("banner.txt").read_text(encoding="utf-8")
    except Exception:
        return "Datadog"


@app.command("guaf", help=t("Easter egg: imprime logo ASCII de Datadog", "Easter egg: print Datadog ASCII logo"))
def guaf() -> None:
    _console.print(_load_banner(), style="bold magenta")

