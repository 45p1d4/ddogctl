import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


def _default_config_path() -> Path:
    return Path(os.path.expanduser("~")) / ".config" / "ddctl" / "config.yaml"


def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg_path = Path(path).expanduser() if path else _default_config_path()
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Error al parsear YAML de config: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("El archivo de configuración debe contener un mapeo YAML.")
    return data


def resolve_context(
    context_name: Optional[str],
    config_path: Optional[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Resolve (site, api_key, app_key) with priority:
    1) Environment variables: DD_SITE, DD_API_KEY, DD_APP_KEY
    2) YAML context in config (~/.config/ddctl/config.yaml by default)
    """
    env_site = os.environ.get("DD_SITE")
    env_api_key = os.environ.get("DD_API_KEY")
    env_app_key = os.environ.get("DD_APP_KEY")

    if env_site or env_api_key or env_app_key:
        # If any credential exists in env, prefer env (missing values remain None)
        site = env_site or ""
        api_key = env_api_key
        app_key = env_app_key
        if not site:
            # Allow site to come from config if not defined in env
            cfg = load_config(config_path)
            ctxs = (cfg.get("contexts") or {}) if isinstance(cfg, dict) else {}
            ctx = {}
            if context_name and isinstance(ctxs, dict):
                ctx = ctxs.get(context_name) or {}
            site = (ctx or {}).get("site") or ""
        if not site:
            raise RuntimeError(
                "Could not resolve 'site'. Define it in DD_SITE or in the YAML context."
            )
        return site, api_key, app_key

    # If no env credentials, try YAML
    cfg = load_config(config_path)
    contexts = cfg.get("contexts") if isinstance(cfg, dict) else None
    if not contexts or not isinstance(contexts, dict):
        raise RuntimeError(
            "No credentials in env and no contexts in YAML. "
            "Set DD_SITE/DD_API_KEY/DD_APP_KEY or create the configuration file."
        )
    selected = context_name or "prd"
    ctx = contexts.get(selected)
    if not ctx or not isinstance(ctx, dict):
        raise RuntimeError(
            f"Context '{selected}' not found in configuration file."
        )
    site = ctx.get("site") or ""
    api_key = ctx.get("api_key")
    app_key = ctx.get("app_key")
    if not site:
        raise RuntimeError(
            f"Context '{selected}' is missing 'site'."
        )
    return site, api_key, app_key

