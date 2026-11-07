"""
Stable, machine-readable error classification for agent consumers.

Every failure reported with `--json` carries:

- `code`        a stable identifier agents can branch on (see ERROR_CODES)
- `retryable`   whether retrying the same command unchanged can succeed
- `suggestions` runnable follow-up commands / hints

Unknown codes must be treated as `error` (non-retryable): read `message`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# code -> (retryable, meaning)
ERROR_CODES: Dict[str, Tuple[bool, str]] = {
    "auth_required": (False, "HTTP 401: API/application key missing, invalid or for another site"),
    "permission_denied": (False, "HTTP 403: the application key lacks the permission for this API"),
    "not_found": (False, "HTTP 404: the resource or endpoint does not exist"),
    "bad_request": (False, "HTTP 400/422: the API rejected the request shape or query"),
    "conflict": (False, "HTTP 409: concurrent or duplicate change"),
    "timeout": (True, "HTTP 408 or client-side timeout: narrow the time range / limit and retry"),
    "rate_limited": (True, "HTTP 429: back off, then retry"),
    "server_error": (True, "HTTP 5xx: Datadog-side failure, retry with backoff"),
    "network_error": (True, "Could not reach the Datadog API (DNS, proxy, connectivity)"),
    "validation_error": (False, "Local flag/argument validation failed"),
    "config_error": (False, "Credentials or config file missing/invalid"),
    "safety_blocked": (False, "Operation not allowed by the context's safety level"),
    "error": (False, "Unclassified failure"),
}


def _status_code(status: int) -> str:
    if status == 401:
        return "auth_required"
    if status == 403:
        return "permission_denied"
    if status == 404:
        return "not_found"
    if status in (400, 422):
        return "bad_request"
    if status == 408:
        return "timeout"
    if status == 409:
        return "conflict"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server_error"
    return "error"


def classify(exc: BaseException) -> str:
    """Map an exception to a stable error code."""
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code in ERROR_CODES:
        return code
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return _status_code(status)
    try:
        import click

        if isinstance(exc, click.exceptions.UsageError) or isinstance(exc, click.exceptions.BadParameter):
            return "validation_error"
    except Exception:  # pragma: no cover - click always present with typer
        pass
    try:
        import requests

        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout"
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "network_error"
    except Exception:  # pragma: no cover
        pass
    if isinstance(exc, ValueError):
        # e.g. dateutil failing to parse --from/--to
        return "validation_error"
    if isinstance(exc, RuntimeError) and getattr(exc, "is_config_error", False):
        return "config_error"
    return "error"


def suggestions_for(code: str, cmd: str) -> List[str]:
    """Follow-up commands an agent (or human) can run next."""
    cli_path = "ddogctl " + " ".join(p.replace("_", "-") for p in cmd.split(".")) if cmd else "ddogctl"
    if code == "auth_required":
        return [
            "ddogctl doctor",
            "Check DD_SITE matches the org's region (datadoghq.com, datadoghq.eu, us3/us5/ap1.datadoghq.com)",
        ]
    if code == "permission_denied":
        return [
            "ddogctl doctor  # lists which API areas this application key can read",
            "Grant the missing permission to the application key's role, or use a key with that scope",
        ]
    if code == "not_found":
        return [f"{cli_path} --help", "Verify the ID/name; list resources first (e.g. `ddogctl monitors list`)"]
    if code == "bad_request":
        return [f"{cli_path} --help", "Re-run with --debug to see the request payload and API error"]
    if code in ("rate_limited", "server_error"):
        return ["Retry with exponential backoff (e.g. 2s, 4s, 8s)"]
    if code == "timeout":
        return ["Narrow --from/--to or lower --limit, then retry"]
    if code == "network_error":
        return ["ddogctl doctor", "Check connectivity/proxy to https://api.<DD_SITE>"]
    if code == "validation_error":
        return [f"{cli_path} --help"]
    if code == "config_error":
        return [
            "Set DD_SITE, DD_API_KEY and DD_APP_KEY, or create ~/.config/ddctl/config.yaml",
            "ddogctl doctor",
        ]
    if code == "safety_blocked":
        return ["Ask a human to change `safety_level` for this context; do not retry"]
    return []


def describe_error(exc: BaseException, cmd: str, message: str) -> Dict[str, Any]:
    code = classify(exc)
    retryable, _ = ERROR_CODES.get(code, ERROR_CODES["error"])
    err: Dict[str, Any] = {
        "code": code,
        "type": exc.__class__.__name__,
        "message": message,
        "retryable": retryable,
    }
    status: Optional[int] = getattr(exc, "status_code", None)
    if status is not None:
        err["status"] = status
    payload = getattr(exc, "payload", None)
    if payload is not None:
        err["payload"] = payload
    sugg = suggestions_for(code, cmd)
    extra = getattr(exc, "suggestions", None)
    if isinstance(extra, list):
        sugg = [*extra, *sugg]
    if sugg:
        err["suggestions"] = sugg
    return err
