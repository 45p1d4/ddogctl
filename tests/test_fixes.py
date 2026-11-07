from __future__ import annotations

import json

from typer.testing import CliRunner

from ddctl.cli import app
from ddctl.commands.metrics import _point_values
from ddctl.commands.url import _site_to_base
from ddctl.normalize import duration_to_ms, normalize_span, ts_to_iso

EPOCH_S = 1_700_000_000  # 2023-11-14T22:13:20Z


def test_ts_to_iso_all_units() -> None:
    expected = "2023-11-14T22:13:20Z"
    assert ts_to_iso(EPOCH_S) == expected
    assert ts_to_iso(EPOCH_S * 1_000) == expected          # ms
    assert ts_to_iso(EPOCH_S * 1_000_000) == expected      # µs (was 1970)
    assert ts_to_iso(EPOCH_S * 1_000_000_000) == expected  # ns


def test_duration_is_nanoseconds() -> None:
    assert duration_to_ms(5_000_000) == 5.0        # 5 ms span (was 5000)
    assert duration_to_ms(250_000_000) == 250.0
    assert duration_to_ms(None) == 0.0
    span = {"attributes": {"custom": {"duration": 12_500_000}}}
    assert normalize_span(span)["dur_ms"] == 12.5


def test_site_to_base() -> None:
    assert _site_to_base("datadoghq.com") == "https://app.datadoghq.com"
    assert _site_to_base("datadoghq.eu") == "https://app.datadoghq.eu"
    assert _site_to_base("us5.datadoghq.com") == "https://us5.datadoghq.com"
    assert _site_to_base("ap1.datadoghq.com") == "https://ap1.datadoghq.com"


def test_point_values_skip_nulls() -> None:
    assert _point_values([[1, 1.5], [2, None], [3, 2]]) == [1.5, 2.0]


def test_errors_are_reported_as_json(monkeypatch) -> None:
    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    res = CliRunner().invoke(app, ["--json", "logs", "query", "--from", "now", "--to", "-1h"])
    assert res.exit_code == 1
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["cmd"] == "logs.query"
    assert "--to" in out["error"]["message"]


def test_timeseries_rejects_zero_interval(monkeypatch) -> None:
    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    res = CliRunner().invoke(app, ["--json", "apm", "trace", "timeseries", "--query", "*", "--interval", "0m"])
    assert res.exit_code == 1
    assert "--interval" in json.loads(res.stdout.strip().splitlines()[-1])["error"]["message"]


def test_apply_rollup() -> None:
    from ddctl.commands.metrics import _apply_rollup

    q = "sum:kubernetes.cpu.requests{kube_cluster_name:van-prd-rke2-1,env:prd}"
    assert _apply_rollup(q, 60) == q + ".rollup(avg,60)"
    assert _apply_rollup("avg:system.cpu.user{*} by {host}", 300).endswith("by {host}.rollup(avg,300)")
    assert _apply_rollup(q + ".rollup(max,30)", 60) == q + ".rollup(max,30)"  # user's own rollup wins
    assert _apply_rollup("a{*} / b{*}", 60) == "a{*} / b{*}"  # formula: untouched
    assert _apply_rollup(q, None) == q


def test_incidents_list_state_uses_server_side_search(monkeypatch) -> None:
    from ddctl.api import ApiClient

    calls = []

    def fake_get(self, path, params=None):
        calls.append((path, params))
        return {"data": {"attributes": {"total": 56, "incidents": [
            {"data": {"id": "a1", "attributes": {"title": "x", "state": "active"}}},
        ]}}}

    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    monkeypatch.setattr(ApiClient, "get", fake_get)
    res = CliRunner().invoke(app, ["--json", "incidents", "list", "--state", "active"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert calls[0][0] == "/api/v2/incidents/search"
    assert calls[0][1]["query"] == "state:active"
    assert out["n"] == 1 and out["data"][0]["state"] == "active"
    assert out["meta"]["total"] == 56


def _k8s_fake(series_by_metric):
    def fake_get(self, path, params=None):
        q = params["query"]
        for metric, points in series_by_metric.items():
            if metric + "{" in q:
                return {"series": [{"pointlist": points}]}
        return {"series": []}
    return fake_get


def test_k8s_usage_is_avg_and_max(monkeypatch) -> None:
    from ddctl.api import ApiClient

    gib = 1024 ** 3
    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    monkeypatch.setattr(ApiClient, "get", _k8s_fake({
        "kubernetes.cpu.requests": [[1, 2.0], [2, 2.0]],
        "kubernetes.cpu.limits": [[1, 4.0]],
        # spiky usage in nanocores: 0.2, 1.8, null, 0.4 cores
        "kubernetes.cpu.usage.total": [[1, 0.2e9], [2, 1.8e9], [3, None], [4, 0.4e9]],
        "kubernetes.memory.requests": [[1, 10 * gib]],
        "kubernetes.memory.limits": [[1, 20 * gib]],
        "container.memory.usage": [[1, 5 * gib], [2, 15 * gib]],
    }))
    res = CliRunner().invoke(app, ["--json", "metrics", "k8s-resources", "--cluster", "c", "--kube-deployment", "d"])
    assert res.exit_code == 0, res.output
    cpu, mem = json.loads(res.stdout.strip().splitlines()[-1])["data"]
    assert round(cpu["usage_avg"], 3) == 0.8 and round(cpu["usage_max"], 3) == 1.8
    assert round(cpu["usage_last"], 3) == 0.4
    assert cpu["usage_pct_of_requests"] == 40.0 and cpu["unit"] == "cores"
    assert mem["usage_pct_of_requests"] == 100.0 and mem["unit"] == "bytes"


def test_k8s_no_data_hint_and_json_param_error(monkeypatch) -> None:
    from ddctl.api import ApiClient

    monkeypatch.setenv("DD_SITE", "datadoghq.com")
    monkeypatch.setenv("DD_API_KEY", "x")
    monkeypatch.setattr(ApiClient, "get", _k8s_fake({}))
    res = CliRunner().invoke(app, ["--json", "metrics", "k8s-resources", "--cluster", "c", "--kube-deployment", "nope"])
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert res.exit_code == 0 and out["meta"]["no_data"] is True
    assert "kube_deployment" in out["meta"]["hint"]

    res = CliRunner().invoke(app, ["--json", "metrics", "k8s-resources", "--cluster", "c"])
    assert res.exit_code == 1
    err = json.loads(res.stdout.strip().splitlines()[-1])["error"]
    assert err["type"] == "BadParameter" and "--kube-" in err["message"]
