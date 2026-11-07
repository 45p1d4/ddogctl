from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from ddctl.api import ApiClient, ApiError
from ddctl.cli import app
from ddctl.errors import ERROR_CODES, classify


def _last_json(res):
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("status,code,retryable", [
    (401, "auth_required", False), (403, "permission_denied", False), (404, "not_found", False),
    (400, "bad_request", False), (408, "timeout", True), (429, "rate_limited", True), (503, "server_error", True),
])
def test_http_errors_map_to_stable_codes(monkeypatch, status, code, retryable) -> None:
    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")

    def boom(self, path, params=None):
        raise ApiError(status, {"errors": ["nope"]})

    monkeypatch.setattr(ApiClient, "get", boom)
    res = CliRunner().invoke(app, ["--json", "monitors", "list"])
    assert res.exit_code == 1
    err = _last_json(res)["error"]
    assert err["code"] == code and err["retryable"] is retryable and err["status"] == status
    assert code in ERROR_CODES
    if code in ("auth_required", "permission_denied"):
        assert any("doctor" in s for s in err["suggestions"])


def test_config_error_code(monkeypatch, tmp_path) -> None:
    for k in ("DD_SITE", "DD_API_KEY", "DD_APP_KEY"):
        monkeypatch.delenv(k, raising=False)
    res = CliRunner().invoke(app, ["--json", "--config", str(tmp_path / "none.yaml"), "monitors", "list"])
    assert res.exit_code == 1
    assert _last_json(res)["error"]["code"] == "config_error"


def test_validation_error_text_mode_has_hint(monkeypatch) -> None:
    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    res = CliRunner().invoke(app, ["logs", "query", "--from", "now", "--to", "-1h"])
    assert res.exit_code == 1
    assert "[validation_error]" in res.stderr and "hint:" in res.stderr


def test_classify_unknown_is_error() -> None:
    assert classify(KeyError("x")) == "error"


def test_commands_catalog() -> None:
    res = CliRunner().invoke(app, ["--json", "commands"])
    assert res.exit_code == 0
    out = _last_json(res)
    paths = {c["path"]: c for c in out["data"]}
    assert "security endpoint-scan" in paths and "doctor" in paths
    assert paths["downtimes schedule"]["mutating"] is True
    assert paths["apm spans list"]["mutating"] is False
    assert any("--json" in o["flags"] for o in out["meta"]["global_options"])
    flags = {fl for o in paths["metrics k8s-resources"]["options"] for fl in o["flags"]}
    assert {"--cluster", "--kube-deployment", "--rollup"} <= flags


def test_doctor_detects_wrong_site(monkeypatch) -> None:
    import ddctl.commands.doctor as doctor

    monkeypatch.setenv("DD_SITE", "datadoghq.eu")
    monkeypatch.setenv("DD_API_KEY", "abcdefgh1234")
    monkeypatch.setenv("DD_APP_KEY", "zyxwvuts9876")
    monkeypatch.setattr(doctor, "_validate_key", lambda site, key: 200 if site == "datadoghq.com" else 403)
    res = CliRunner().invoke(app, ["--json", "doctor"])
    assert res.exit_code == 1
    checks = {c["check"]: c for c in _last_json(res)["data"]}
    assert checks["api_key"]["status"] == "fail"
    assert checks["api_key"]["detected_site"] == "datadoghq.com"
    assert "1234" in checks["credentials"]["detail"] and "abcdefgh" not in checks["credentials"]["detail"]


def test_doctor_reports_forbidden_areas(monkeypatch) -> None:
    import ddctl.commands.doctor as doctor

    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "abcdefgh1234")
    monkeypatch.setenv("DD_APP_KEY", "zyxwvuts9876")
    monkeypatch.setattr(doctor, "_validate_key", lambda site, key: 200)

    def fake_get(self, path, params=None):
        if path.startswith("/api/v1/hosts"):
            raise ApiError(403, {"errors": ["Forbidden"]})
        return {}

    monkeypatch.setattr(ApiClient, "get", fake_get)
    monkeypatch.setattr(ApiClient, "post", lambda self, path, json=None: {})
    res = CliRunner().invoke(app, ["--json", "doctor"])
    assert res.exit_code == 0  # warnings only
    checks = {c["check"]: c for c in _last_json(res)["data"]}
    assert checks["access:hosts"]["status"] == "warn" and checks["access:hosts"]["detail"] == "forbidden"
    assert checks["access:monitors"]["status"] == "ok"
    res = CliRunner().invoke(app, ["--json", "doctor", "--strict"])
    assert res.exit_code == 1
