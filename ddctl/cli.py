from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .api import ApiClient
from .i18n import t
from .options import DebugOption

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
    json_output: bool = typer.Option(
        False, "--json", "-j",
        help=t("Emitir JSON en stdout (suprime tablas)", "Emit JSON to stdout (suppress tables)"),
    ),
    full: bool = typer.Option(
        False, "--full",
        help=t("Emitir payload Datadog completo sin whitelist (con --json)", "Emit raw Datadog payload without whitelist (with --json)"),
    ),
) -> None:
    # Global options only stored in context
    _ensure_ctx(ctx)
    ctx.obj["context_name"] = context
    ctx.obj["config_path"] = str(config) if config else None
    ctx.obj["json"] = json_output
    ctx.obj["full"] = full


def get_client_from_ctx(ctx: typer.Context) -> ApiClient:
    _ensure_ctx(ctx)
    context_name = ctx.obj.get("context_name")
    config_path = ctx.obj.get("config_path")
    # No caching para simplicidad y evitar inconsistencias con cambios de opciones
    try:
        return ApiClient.create_from_context(context_name, config_path)
    except Exception as exc:
        # Config/credential problems: report cleanly instead of a traceback.
        from .ui import cmd_name_from_ctx, fail
        fail(ctx, cmd_name_from_ctx(ctx), exc)


# Registrar sub-apps
from .commands import auth as auth_cmd  # noqa: E402
from .commands import monitors as monitors_cmd  # noqa: E402
from .commands import dashboards as dashboards_cmd  # noqa: E402
from .commands import incidents as incidents_cmd  # noqa: E402
from .commands import synthetics as synthetics_cmd  # noqa: E402
from .commands import logs as logs_cmd  # noqa: E402
from .commands import apm as apm_cmd  # noqa: E402
from .commands import services as services_cmd  # noqa: E402
from .commands import service as service_cmd  # noqa: E402
from .commands import metrics as metrics_cmd  # noqa: E402
from .commands import rum as rum_cmd  # noqa: E402
from .commands import hosts as hosts_cmd  # noqa: E402
from .commands import downtimes as downtimes_cmd  # noqa: E402
from .commands import url as url_cmd  # noqa: E402
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
app.add_typer(service_cmd.app, name="service")
app.add_typer(metrics_cmd.app, name="metrics")
app.add_typer(rum_cmd.app, name="rum")
app.add_typer(hosts_cmd.app, name="hosts")
app.add_typer(downtimes_cmd.app, name="downtimes")
app.add_typer(url_cmd.app, name="url")

from .commands import catalog as catalog_cmd  # noqa: E402
from .commands import doctor as doctor_cmd  # noqa: E402
from .commands import security as security_cmd  # noqa: E402

app.add_typer(security_cmd.app, name="security")
doctor_cmd.register(app)
catalog_cmd.register(app)

_console = Console()

def _load_banner() -> str:
    try:
        return importlib_resources.files("ddctl").joinpath("banner.txt").read_text(encoding="utf-8")
    except Exception:
        return "Datadog"


@app.command("guaf", help=t("Easter egg: imprime logo ASCII de Datadog", "Easter egg: print Datadog ASCII logo"))
def guaf(debug: DebugOption = False) -> None:
    _console.print(_load_banner(), style="bold magenta")



def run() -> None:
    """
    Console-script entry point.

    On Windows, click expands glob patterns in argv by default, so a span or
    log query like `--query "*"` was silently replaced by the list of files in
    the current directory ("Got unexpected extra arguments"). Datadog queries
    use `*` constantly, so disable that expansion.
    """
    app(windows_expand_args=False)


if __name__ == "__main__":
    run()
