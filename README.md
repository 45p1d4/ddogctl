<div align="center">

# ddogctl

**The Datadog CLI inspired by `kubectl` — fast, scriptable, and built for AI agents.**

`ddogctl` brings the ergonomics of `kubectl` to Datadog: predictable subcommands,
rich tables for humans, and compact JSON for machines (and agents).
It covers Monitors, Dashboards, Incidents, Synthetics, Logs, APM (spans / errors / traces),
RUM, Metrics, Service Catalog, Hosts, Downtimes and deep‑link URL generation —
all behind a single binary, with bilingual help (English / Spanish).

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Typer](https://img.shields.io/badge/built%20with-Typer-009485)](https://typer.tiangolo.com/)
[![Rich](https://img.shields.io/badge/output-Rich%20tables-orange)](https://rich.readthedocs.io/)
[![Datadog](https://img.shields.io/badge/Datadog-API-632CA6?logo=datadog&logoColor=white)](https://docs.datadoghq.com/api/)
[![Agent‑friendly](https://img.shields.io/badge/Claude%20agents-optimized-7C3AED)](#claude-agents--ai-friendly-output)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](#contributing)

[Read this in Spanish / Léelo en español →](README.es.md)

</div>

---

## Table of contents

- [Why ddogctl?](#why-ddogctl)
- [Claude agents / AI‑friendly output](#claude-agents--ai-friendly-output)
- [Quickstart](#quickstart)
- [Authentication](#authentication)
- [Global options](#global-options)
- [Command reference](#command-reference)
  - [auth](#auth) · [monitors](#monitors) · [dashboards](#dashboards) · [incidents](#incidents)
  - [synthetics](#synthetics) · [logs](#logs) · [apm](#apm) · [service](#service-troubleshoot)
  - [services](#services-software-catalog) · [metrics](#metrics) · [rum](#rum)
  - [hosts](#hosts) · [downtimes](#downtimes) · [url](#url-deep-links-no-api-calls)
- [Recipes](#recipes)
- [Contributing](#contributing)
- [License](#license)

---

## Why ddogctl?

- **One CLI, the whole platform.** Monitors, dashboards, incidents, synthetics, logs, APM, RUM, metrics, hosts, downtimes, service catalog and deep‑link URLs.
- **Two output modes.** Rich tables in your terminal, or `--json` for pipelines and AI agents — with `--full` to escape‑hatch the raw Datadog payload.
- **Built for SRE workflows.** `service troubleshoot` consolidates four internal calls (error rate, p95, top error resources, recent error logs) into a single sub‑1.5 KB payload.
- **kubectl ergonomics.** Stable noun‑verb commands, named flags, contexts, `--from / --to` time ranges (`now-1h`, `-15m`, ISO datetimes), and a `--debug` flag on every leaf.
- **Multi‑context** with a single `~/.config/ddctl/config.yaml`, switchable with `--context`. Env vars (`DD_SITE`, `DD_API_KEY`, `DD_APP_KEY`) take precedence.
- **Bilingual help.** Set `DDOGCTL_LANG=en` or `DDOGCTL_LANG=es`.
- **Easter egg.** `ddogctl guaf` prints a Datadog ASCII banner.

> Like `kubectl get pods` but for Datadog, with a side of agent‑ready JSON.

---

## Claude agents / AI‑friendly output

`ddogctl` is **first‑class for LLM tool‑use** (Claude agents, Claude Code, custom assistants):

- **Compact JSON** with `--json`: `{cmd, n, data, meta}`, single‑line, no whitespace, stable field names.
- **Whitelisted by default**: each command publishes a curated field set tailored to the question being asked (see `DEFAULT_FIELDS` in `ddctl/normalize.py`).
- **Escape hatch**: add `--full` to keep the full Datadog payload when you really need it.
- **Errors are structured too**: `{cmd, error:{type, message, status, payload}}` — agents can branch on `status` without parsing prose.
- **Token‑efficient**: in internal SRE triage benchmarks, `ddogctl --json` payloads were **up to ~88% smaller** than the equivalent calls through the official Datadog MCP server, reducing both cost and latency.

Minimal Claude tool definition (Anthropic Tools API / Claude Code):

```json
{
  "name": "ddogctl_service_troubleshoot",
  "description": "One-shot Datadog service triage (error rate, p95, top error resources, recent error logs).",
  "input_schema": {
    "type": "object",
    "required": ["service"],
    "properties": {
      "service":  {"type": "string"},
      "env":      {"type": "string", "default": "prd"},
      "from":     {"type": "string", "default": "now-15m"},
      "context":  {"type": "string", "default": "prd"}
    }
  }
}
```

Then run, on the agent side:

```bash
ddogctl --json --context prd service troubleshoot \
  --service "$SERVICE" --env "$ENV" --from "$FROM"
```

A full triage recipe lives in [`examples/triage-with-json.md`](examples/triage-with-json.md).

---

## Quickstart

```bash
pip install -e .

# Credentials (PowerShell)
$env:DD_SITE   = "datadoghq.com"
$env:DD_API_KEY = "<YOUR_API_KEY>"
$env:DD_APP_KEY = "<YOUR_APP_KEY>"

ddogctl auth status
ddogctl monitors list --states "Alert,Warn" --page-size 50
ddogctl service troubleshoot --service checkout --env prd --from now-15m
ddogctl --json apm errors rate --service checkout --env prd --from now-1h --group-by resource_name
```

> macOS / Linux: replace `$env:VAR = "..."` with `export VAR=...`.

---

## Authentication

Credentials are resolved with the following priority:

1. **Environment variables**: `DD_SITE`, `DD_API_KEY`, `DD_APP_KEY`.
2. **YAML config**: `~/.config/ddctl/config.yaml`.

```yaml
contexts:
  prd:
    site: datadoghq.com
    api_key: "YOUR_API_KEY"
    app_key: "YOUR_APP_KEY"
  staging:
    site: datadoghq.eu
    api_key: "..."
    app_key: "..."
```

Pick a context with `--context staging` and override the path with `--config ./other.yaml`.
Most endpoints (monitors, dashboards, incidents, synthetics, logs, APM, RUM, metrics)
**require an Application Key** in addition to the API key.

---

## Global options

| Option | Description |
|---|---|
| `--context <name>` | YAML context to use. |
| `--config <path>`  | Path to YAML config file. |
| `--json`, `-j`     | Emit compact JSON to stdout (suppresses tables). |
| `--full`           | When combined with `--json`, emit the raw Datadog payload (no whitelist). |
| `DDOGCTL_LANG=en\|es` | Help language (defaults to `es`; set `en` for English help). |
| `--debug` (per command) | Verbose, non‑secret debugging info — available on every leaf command. |

---

## Command reference

> Every command supports `--json`, `--full`, `--context`, `--config` and `--debug`.

### auth
```bash
ddogctl auth status            # GET /api/v1/validate
```

### monitors
```bash
ddogctl monitors list \
  [--name <substring>] [--tags service:my-svc,env:prd] \
  [--monitor-tags team:platform] [--states "Alert,Warn,No Data,OK"] \
  [--page-size 100]

ddogctl monitors mute --id <int>
```

### dashboards
```bash
ddogctl dashboards get --id <id>
```

### incidents
```bash
ddogctl incidents create --title "Title" --severity SEV-2 \
  [--summary "..."] [--root-cause "..."] [--detection-method "Monitor"] \
  [--service svc] [--team platform] [--customer-impact]
```

### synthetics
```bash
ddogctl synthetics trigger --public-id <id> --public-id <id2>
```

### logs
```bash
ddogctl logs query --from -1h --to now \
  [--service payments] [--query "status:error"] [--limit 50]
```
Columns: `timestamp`, `service`, `status`, `message` (truncated to 400 chars).
Time parser accepts `now`, `now-15m`, `-15m`, `-1h`, `-2d`, and ISO datetimes.

### apm
```bash
# Spans
ddogctl apm spans list   --service my-svc --env prd --from now-15m --limit 50
ddogctl apm spans search --query "service:my-svc env:prd" --from now-1h --limit 50
ddogctl apm spans aggregate --query "service:my-svc" --group-by resource_name --from now-1h

# Error analytics
ddogctl apm errors top-resources --service my-svc --env prd --from now-1h --limit 10
ddogctl apm errors rate          --service my-svc --env prd --from now-1h --group-by resource_name

# Traces
ddogctl apm trace get        --trace-id <id>
ddogctl apm trace timeseries --query "service:my-svc env:prd" --from now-1h
```

Notes:
- Auto‑hides empty columns; `env` / `service` move to the table title when constant.
- Durations are reported in milliseconds (`dur_ms`); APM count is read from `attributes.compute.c0`.

### service troubleshoot
**The flagship SRE command** — APM error rate + p95 latency, top error resources, last error logs, all in one record:
```bash
ddogctl service troubleshoot \
  --service checkout --env prd --from now-1h \
  [--cluster your_cluster] [--debug]
```

### services (Software Catalog)
```bash
ddogctl services apply --file ./service.yaml
ddogctl services apply --service my-svc --schema-version v2.1 --env prd \
  --description "Checkout service" --tag team:platform --tag app:web --tier critical
ddogctl services list                # table by default
ddogctl services get    --service my-svc
ddogctl services delete --service my-svc
```

### metrics
```bash
# Timeseries with inline sparkline
ddogctl metrics query \
  --query "avg:kubernetes.cpu.requests{cluster:my} by {kube_deployment}" \
  --from now-1h --rollup 120 --limit 20 --spark

# Tag cardinality
ddogctl metrics tag-cardinality --metric kubernetes.cpu.requests

# Kubernetes capacity (CPU/Memory) per service or deployment
ddogctl metrics k8s-resources \
  --cluster your_cluster_name \
  --kube-service your_service_name \
  --from now-30m --rollup 120 [--cpu-unit mcores] [--debug]
```
CPU is rendered in cores or mCores (no scientific notation); memory is auto‑scaled (B / KiB / MiB / GiB).

### rum
```bash
ddogctl rum apps list
ddogctl rum events search --query "@type:error" --from now-1h --limit 20
ddogctl rum events count  --group-by @type --from now-2h
```

### hosts
```bash
ddogctl hosts list   [--filter "env:prd OR cluster:tor"] [--count 20]
ddogctl hosts count
ddogctl hosts mute   --host <name> [--message "patching"] [--end <epoch>] [--override]
ddogctl hosts unmute --host <name>
```

### downtimes
```bash
ddogctl downtimes list [--current-only]
ddogctl downtimes schedule --scope "env:prd" --start 2026-05-12T00:00:00Z --end 2026-05-12T01:00:00Z --message "Release"
ddogctl downtimes cancel --id <id>
```

### url (deep links, no API calls)
Generate share‑ready links to the Datadog UI without burning an API call:
```bash
ddogctl url trace     --trace-id <id> --from now-1h --to now
ddogctl url explorer  --query "service:my-svc env:prd" --from now-24h
ddogctl url service   --service my-svc --env prd --from now-24h
ddogctl url logs      --query "status:error service:my-svc" --from now-1h
ddogctl url monitor   --id 42
ddogctl url dashboard --id abc-123
ddogctl url incident  --id 17
```
Base host is inferred from the active context's `site` (or `--base`).

---

## Recipes

**90‑second production triage**
```bash
SVC=my-api; ENV=prd

ddogctl --json --context prd service troubleshoot --service "$SVC" --env "$ENV" --from -15m
ddogctl --json --context prd monitors list --tags "service:$SVC" --states "Alert,Warn"
ddogctl --json --context prd apm errors rate --service "$SVC" --env "$ENV" \
  --group-by resource_name --from -15m --limit 5 | jq '.data[0:3]'
```

**Hand off a deep link**
```bash
ddogctl url logs --query "service:$SVC status:error" --from now-1h
```

**Pipe into `jq`**
```bash
ddogctl --json apm errors top-resources --service "$SVC" --env "$ENV" --from now-1h --limit 5 \
  | jq '.data[] | "\(.count)\t\(.resource)"'
```

A complete triage walkthrough is in [`examples/triage-with-json.md`](examples/triage-with-json.md).

---

## Contributing

PRs and issues are welcome — especially:

- New commands or flags that map cleanly to Datadog endpoints.
- Token‑efficient response shapes for AI agents.
- Bilingual help text (the project ships with full ES/EN strings via `ddctl/i18n.py`).
- Tests under `tests/` (run with `pytest -q`).

```bash
pip install -e .
pip install pytest
pytest -q
```

A guardrail test (`tests/test_debug_help.py`) ensures every leaf command exposes `--debug`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for a full walkthrough — architecture cheat sheet, how to add a new command, output contract, style and commit/PR conventions.

---

## License

This project is licensed under the [MIT License](LICENSE).

If `ddogctl` saves you time, please consider giving the repo a ⭐ — it really helps surface the project to other Datadog users and AI builders.
