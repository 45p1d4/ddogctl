```bash
ddogctl metrics k8s-resources \
  --cluster your_cluster_name \
  --kube-service your_service_name \
  --from now-30m --rollup 120 \
  [--cpu-unit mcores] [--debug]
```# ddogctl

A minimal Datadog CLI inspired by kubectl, built with Typer and Rich.

## Requirements
- Python 3.10+

## Installation (editable)

```bash
pip install -e .
```

## Environment variables
- `DD_SITE` (e.g., `datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`, ...)
- `DD_API_KEY`
- `DD_APP_KEY`

Credentials are read from environment variables first. If any value is missing, they are resolved from a YAML context.

## Configuration file
Default path: `~/.config/ddctl/config.yaml`

Format:
```yaml
contexts:
  prd:
    site: datadoghq.com
    api_key: "YOUR_API_KEY"
    app_key: "YOUR_APP_KEY"
```

You can select a context with `--context <name>` and override the file path with `--config <path>`.

## Global options
- `--context <name>`: YAML context to use.
- `--config <path>`: YAML config file to use.
- `DDOGCTL_LANG=en|es`: Help language (defaults to `es`). Set to `en` for English help.

## Commands

### Auth
Validate keys and print site and API key status:
```bash
ddogctl auth status
```

### Monitors
List monitors (optional substring filter):
```bash
ddogctl monitors list [--name <substring>]
```

Mute a monitor:
```bash
ddogctl monitors mute --id <int>
```

### Dashboards
Get a dashboard by ID:
```bash
ddogctl dashboards get --id <id>
```

### Incidents
Create an incident (defaults severity to SEV-2):
```bash
ddogctl incidents create --title "Title" --severity SEV-2
```

### Synthetics
Trigger one or more tests by public id:
```bash
ddogctl synthetics trigger --public-id <id> --public-id <id2>
```

### Logs
Query logs (relative times like `-15m`, `-1h`, `-2d` and ISO datetimes are supported):
```bash
ddogctl logs query \
  --from -1h --to now \
  [--service payments] \
  [--query "status:error"] \
  [--limit 50]
```
Printed columns: `timestamp`, `service`, `status`, `message` (message truncated to 400 chars).

### APM - Spans
List spans (simple GET) with optional service/env filter:
```bash
ddogctl apm spans list --service my-service --env prd --from now-15m --limit 50
```

Advanced search (POST) with Trace Explorer query:
```bash
ddogctl apm spans search --query "service:my-service env:prd" --from now-1h --limit 50
```

Notes:
- Time parser accepts `now`, `now-15m`, `-15m`, `-1h`, `-2d`, and ISO datetimes.
- Table auto-hides empty columns; `env` and `service` move to the title when they’re constant.
- Timestamps are shown as `HH:MM:SS` and the table title includes the date.
- Durations are rendered in seconds (`duration_s`).
- Use `--debug` to show raw items for troubleshooting.

### APM - Error aggregates
Top resources by error count (supports `--env` and `--debug`):
```bash
ddogctl apm errors top-resources --service my-service --env prd --from now-1h --limit 10 --debug
```

Error counts grouped by a facet (default `resource_name`):
```bash
ddogctl apm errors rate --service my-service --group-by resource_name --from now-1h --env prd --debug
```

The aggregates endpoint is called with JSON:API `data.type=aggregate_request`. Count is read from `attributes.compute.c0` (or from the legacy `computes[0].value` when applicable).

### Easter egg
Print a Datadog ASCII banner:
```bash
ddogctl guaf
```

### Services (Service Definitions)
Create or update from a YAML file:
```bash
ddogctl services apply --file ./service.yaml
```

Create or update from flags (minimal definition):
```bash
ddogctl services apply \
  --service my-service \
  --schema-version v2.1 \
  --env prd \
  --description "Checkout service" \
  --tag team:platform --tag app:web --tier critical
```

List service definitions (table by default; use `--debug` for raw JSON):
```bash
ddogctl services list
ddogctl services list --debug
```

Get a single service definition:
```bash
ddogctl services get --service my-service
```

Delete a service definition:
```bash
ddogctl services delete --service my-service
```

See Datadog Service Definition API for details: https://docs.datadoghq.com/es/api/latest/service-definition

### Metrics

Query timeseries:
```bash
ddogctl metrics query --query "avg:kubernetes.cpu.requests{cluster:your_cluster_name} by {kube_deployment}" --from now-1h --rollup 120 --limit 20 --spark
```
Options:
- `--limit`: max series to render
- `--spark` and `--spark-points`: show an inline sparkline
- `--scope-tag kube_deployment`: keep only that scope tag in the table
- Values are printed with fixed-point decimals (no scientific notation)

Tag cardinality:
```bash
ddogctl metrics tag-cardinality --metric kubernetes.cpu.requests
```

Kubernetes resources (CPU/Memory) per service or deployment:
```bash
ddogctl metrics k8s-resources \
  --cluster your_cluster_name \
  --kube-service your_service_name \
  --from now-30m --rollup 120 \
  [--cpu-unit mcores] [--debug]
```
What it shows (latest point in range):
- CPU requests: `sum:kubernetes.cpu.requests{...}`
- CPU limits: `sum:kubernetes.cpu.limits{...}`
- CPU usage: `sum:kubernetes.cpu.usage.total{...}.as_rate()` (converted from nanocores/s to cores, or to mCores with `--cpu-unit mcores`)
- Memory requests: `sum:kubernetes.memory.requests{...}`
- Memory limits: `sum:kubernetes.memory.limits{...}`
- Memory usage: `sum:container.memory.usage{...}` (humanized units B/KiB/MiB/GiB)

Notes:
- All aggregations use `sum` to represent the total footprint of the selected workload.
- CPU printing avoids scientific notation; memory is auto-scaled to human units.

## Quickstart
```bash
# 1) Install
pip install -e .

# 2) Set credentials (PowerShell examples)
$env:DD_SITE="datadoghq.com"
$env:DD_API_KEY="<YOUR_API_KEY>"
$env:DD_APP_KEY="<YOUR_APP_KEY>"

# 3) Validate
ddogctl auth status

# 4) Try some commands
ddogctl monitors list --name cpu
ddogctl dashboards get --id abc-def-123
ddogctl logs query --from -15m --service checkout --query "status:error" --limit 100
ddogctl apm spans search --query "service:my-service env:prd" --from now-1h --limit 50
ddogctl apm errors top-resources --service my-service --env prd --from now-1h --limit 10
ddogctl guaf
```

## Notes
- Many APM fields depend on your instrumentation and indexed spans. The CLI tries multiple attribute locations (`attributes`, `custom`, `tags`) and falls back to `operation_name` when HTTP method isn’t available.
- If a column is fully empty across results, it’s hidden.

