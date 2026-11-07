"""
`ddogctl doctor`: check configuration, connectivity, credentials and which
Datadog API areas the application key can read.

All probes are read-only and cheap (page size 1, 15-minute windows).
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
import typer
from rich.console import Console

from ..api import ApiClient, ApiError
from ..config import _default_config_path, load_config, resolve_context
from ..i18n import t
from ..options import DebugOption
from ..ui import emit, is_json, new_table

console = Console()

KNOWN_SITES = [
    "datadoghq.com",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "datadoghq.eu",
    "ap1.datadoghq.com",
    "ap2.datadoghq.com",
    "ddog-gov.com",
]


def _mask(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return f"****{v[-4:]}" if len(v) > 8 else "****"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _window() -> Tuple[str, str]:
    now = datetime.now(timezone.utc)
    return _iso(now - timedelta(minutes=15)), _iso(now)


# area -> (commands that depend on it, probe)
Probe = Callable[[ApiClient], Any]


def _probes() -> List[Tuple[str, str, Probe]]:
    f, to = _window()
    now_s = int(time.time())
    return [
        ("monitors", "monitors list/mute",
         lambda c: c.get("/api/v1/monitor", params={"page": 0, "page_size": 1})),
        ("logs", "logs query, service troubleshoot",
         lambda c: c.post("/api/v2/logs/events/search",
                          json={"filter": {"from": f, "to": to, "query": "*"}, "page": {"limit": 1}})),
        ("apm_spans", "apm *, service troubleshoot, security *",
         lambda c: c.get("/api/v2/spans/events",
                         params={"filter[from]": f, "filter[to]": to, "page[limit]": 1})),
        ("metrics", "metrics query/k8s-resources",
         lambda c: c.get("/api/v1/query",
                         params={"from": now_s - 300, "to": now_s, "query": "avg:datadog.agent.running{*}"})),
        ("hosts", "hosts list/count/mute/unmute",
         lambda c: c.get("/api/v1/hosts", params={"count": 1})),
        ("incidents", "incidents list/get/create",
         lambda c: c.get("/api/v2/incidents", params={"page[size]": 1})),
        ("teams", "incidents fields (team list)",
         lambda c: c.get("/api/v2/team", params={"page[size]": 1})),
        ("rum", "rum apps/events",
         lambda c: c.get("/api/v2/rum/applications")),
        ("downtimes", "downtimes list/schedule/cancel",
         lambda c: c.get("/api/v2/downtime", params={"page[limit]": 1})),
        ("software_catalog", "services list/get/apply",
         lambda c: c.get("/api/v2/catalog/entity", params={"page[limit]": 1})),
        ("dashboards", "dashboards get",
         lambda c: c.get("/api/v1/dashboard", params={"count": 1})),
        ("synthetics", "synthetics trigger",
         lambda c: c.get("/api/v1/synthetics/tests", params={"page_size": 1})),
    ]


def _config_source(context_name: Optional[str], config_path: Optional[str]) -> Dict[str, Any]:
    env = {k: bool(os.environ.get(k)) for k in ("DD_SITE", "DD_API_KEY", "DD_APP_KEY")}
    cfg_path = os.path.expanduser(config_path) if config_path else str(_default_config_path())
    info: Dict[str, Any] = {
        "source": "env" if any(env.values()) else "yaml",
        "env_vars_set": [k for k, v in env.items() if v],
        "config_file": cfg_path,
        "config_file_exists": os.path.exists(cfg_path),
        "context": context_name or "prd",
    }
    if info["source"] == "env" and info["config_file_exists"]:
        info["note"] = "DD_* environment variables take precedence over the YAML keys"
    try:
        cfg = load_config(config_path)
        ctxs = cfg.get("contexts") or {}
        info["contexts_in_file"] = sorted(ctxs.keys()) if isinstance(ctxs, dict) else []
    except Exception as exc:  # broken YAML is itself a finding
        info["config_file_error"] = str(exc)
    return info


def _check(name: str, status: str, detail: str, **extra: Any) -> Dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, **extra}


def _validate_key(site: str, api_key: Optional[str]) -> Optional[int]:
    try:
        r = requests.get(f"https://api.{site}/api/v1/validate",
                         headers={"DD-API-KEY": api_key or ""}, timeout=10)
        return r.status_code
    except requests.RequestException:
        return None


def run_checks(context_name: Optional[str], config_path: Optional[str]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    src = _config_source(context_name, config_path)
    checks.append(_check(
        "config", "ok" if "config_file_error" not in src else "fail",
        f"credentials from {src['source']} (context {src['context']})"
        + (f"; {src['note']}" if src.get("note") else ""),
        **{k: v for k, v in src.items() if k not in ("note",)},
    ))

    try:
        site, api_key, app_key = resolve_context(context_name, config_path)
    except Exception as exc:
        checks.append(_check("credentials", "fail", str(exc),
                             suggestion="Set DD_SITE/DD_API_KEY/DD_APP_KEY or create the YAML config"))
        return checks, {"site": None}

    checks.append(_check(
        "credentials", "ok" if api_key and app_key else "warn",
        f"site={site} api_key={_mask(api_key) or 'MISSING'} app_key={_mask(app_key) or 'MISSING'}",
        site=site,
    ))

    # Connectivity + API key validity on the configured site
    status = _validate_key(site, api_key)
    if status is None:
        checks.append(_check("connectivity", "fail", f"cannot reach https://api.{site}",
                             suggestion="Check network/proxy, and that DD_SITE is a valid Datadog site"))
        return checks, {"site": site}
    checks.append(_check("connectivity", "ok", f"https://api.{site} reachable"))

    if status == 200:
        checks.append(_check("api_key", "ok", f"valid for {site}"))
    else:
        detected = None
        if status in (401, 403):
            for other in KNOWN_SITES:
                if other != site and _validate_key(other, api_key) == 200:
                    detected = other
                    break
        if detected:
            checks.append(_check("api_key", "fail",
                                 f"rejected by {site} (HTTP {status}) but valid for {detected}",
                                 suggestion=f"Set DD_SITE={detected} (or `site: {detected}` in the YAML context)",
                                 detected_site=detected))
        else:
            checks.append(_check("api_key", "fail", f"rejected by {site} (HTTP {status}) and by every known site",
                                 suggestion="Create a new API key in Organization Settings → API Keys"))
        return checks, {"site": site}

    if not app_key:
        checks.append(_check("app_key", "fail", "missing — almost every command needs an application key",
                             suggestion="Set DD_APP_KEY (Organization Settings → Application Keys)"))
        return checks, {"site": site}

    client = ApiClient(site=site, api_key=api_key, app_key=app_key)
    client._timeout = 15
    area_results: List[Dict[str, Any]] = []
    for area, cmds, probe in _probes():
        try:
            probe(client)
            area_results.append({"area": area, "status": "ok", "commands": cmds})
        except ApiError as e:
            st = "forbidden" if e.status_code == 403 else ("unauthorized" if e.status_code == 401 else f"http_{e.status_code}")
            area_results.append({"area": area, "status": st, "commands": cmds, "http": e.status_code})
        except requests.RequestException as e:
            area_results.append({"area": area, "status": "network_error", "commands": cmds, "detail": str(e)[:120]})

    if all(r["status"] == "unauthorized" for r in area_results):
        checks.append(_check("app_key", "fail", "application key rejected (HTTP 401) on every API",
                             suggestion="Create a new application key; it must belong to the same org as the API key"))
    else:
        checks.append(_check("app_key", "ok", "accepted"))

    blocked = [r for r in area_results if r["status"] != "ok"]
    for r in area_results:
        checks.append(_check(
            f"access:{r['area']}",
            "ok" if r["status"] == "ok" else "warn",
            r["status"] if r["status"] != "ok" else "readable",
            commands=r["commands"],
            **({"suggestion": f"Grant read permission for {r['area']} to the application key's role"}
               if r["status"] == "forbidden" else {}),
        ))
    return checks, {"site": site, "areas_blocked": [r["area"] for r in blocked]}


def register(app: typer.Typer) -> None:
    @app.command(
        "doctor",
        help=t(
            "Verifica config, conectividad, credenciales y permisos por área de la API",
            "Check config, connectivity, credentials and per-area API permissions",
        ),
    )
    def doctor(
        ctx: typer.Context,
        strict: bool = typer.Option(
            False, "--strict",
            help=t("Salir con código 1 también si algún área no es accesible",
                   "Exit 1 also when any API area is not accessible"),
        ),
        debug: DebugOption = False,
    ) -> None:
        obj = ctx.obj or {}
        if not is_json(ctx):
            with console.status("[dim]Ejecutando chequeos[/dim]"):
                checks, summary = run_checks(obj.get("context_name"), obj.get("config_path"))
        else:
            checks, summary = run_checks(obj.get("context_name"), obj.get("config_path"))

        failed = [c for c in checks if c["status"] == "fail"]
        warned = [c for c in checks if c["status"] == "warn"]
        summary.update({"ok": not failed, "failed": len(failed), "warnings": len(warned)})

        def _render() -> None:
            icon = {"ok": "[green]✔[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✖[/red]"}
            tbl = new_table("ddogctl doctor")
            tbl.add_column("", no_wrap=True)
            tbl.add_column("check", style="cyan", no_wrap=True)
            tbl.add_column("detail", style="white")
            tbl.add_column("affects / fix", style="dim")
            for c in checks:
                hint = c.get("suggestion") or (c.get("commands") if c["status"] != "ok" else "") or ""
                tbl.add_row(icon.get(c["status"], c["status"]), c["check"], c["detail"], hint)
            console.print(tbl)
            if failed:
                console.print(f"[red]{len(failed)} check(s) failed[/red]")
            elif warned:
                console.print(f"[yellow]OK with {len(warned)} warning(s)[/yellow]")
            else:
                console.print("[green]All checks passed[/green]")

        emit(ctx, "doctor", checks, meta=summary, table_renderer=_render)
        if failed or (strict and warned):
            raise typer.Exit(code=1)
