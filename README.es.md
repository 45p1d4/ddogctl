<div align="center">

# ddogctl

**El CLI de Datadog inspirado en `kubectl` — rápido, scriptable y diseñado para agentes de IA.**

`ddogctl` lleva la ergonomía de `kubectl` a Datadog: subcomandos predecibles,
tablas Rich para humanos y JSON compacto para máquinas (y agentes).
Cubre Monitors, Dashboards, Incidents, Synthetics, Logs, APM (spans / errores / traces),
RUM, Métricas, Service Catalog, Hosts, Downtimes y generación de URLs profundas a la UI —
todo desde un único binario, con ayuda bilingüe (Español / Inglés).

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Typer](https://img.shields.io/badge/built%20with-Typer-009485)](https://typer.tiangolo.com/)
[![Rich](https://img.shields.io/badge/output-Rich%20tables-orange)](https://rich.readthedocs.io/)
[![Datadog](https://img.shields.io/badge/Datadog-API-632CA6?logo=datadog&logoColor=white)](https://docs.datadoghq.com/api/)
[![Agent‑friendly](https://img.shields.io/badge/Claude%20agents-optimized-7C3AED)](#agentes-de-claude--salida-amigable-para-ia)
[![PRs bienvenidas](https://img.shields.io/badge/PRs-welcome-brightgreen)](#contribuir)

[Read this in English / Leer en inglés →](README.md)

</div>

---

## Tabla de contenido

- [¿Por qué ddogctl?](#por-qué-ddogctl)
- [Agentes de Claude / salida amigable para IA](#agentes-de-claude--salida-amigable-para-ia)
- [Inicio rápido](#inicio-rápido)
- [Autenticación](#autenticación)
- [Opciones globales](#opciones-globales)
- [Referencia de comandos](#referencia-de-comandos)
  - [auth](#auth) · [monitors](#monitors) · [dashboards](#dashboards) · [incidents](#incidents)
  - [synthetics](#synthetics) · [logs](#logs) · [apm](#apm) · [service](#service-troubleshoot)
  - [services](#services-software-catalog) · [metrics](#metrics-métricas) · [rum](#rum)
  - [hosts](#hosts) · [downtimes](#downtimes) · [url](#url-deep-links-sin-llamadas-a-la-api)
- [Recetas](#recetas)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

---

## ¿Por qué ddogctl?

- **Un solo CLI, toda la plataforma.** Monitors, dashboards, incidents, synthetics, logs, APM, RUM, métricas, hosts, downtimes, service catalog y URLs profundas.
- **Dos modos de salida.** Tablas Rich en tu terminal, o `--json` para pipelines y agentes — con `--full` como escape para el payload crudo de Datadog.
- **Pensado para SRE.** `service troubleshoot` consolida cuatro llamadas internas (tasa de error, p95, top recursos en error, logs de error recientes) en un solo registro de menos de 1.5 KB.
- **Ergonomía tipo kubectl.** Subcomandos estables verbo‑sustantivo, flags con nombre, contextos, rangos `--from / --to` (`now-1h`, `-15m`, ISO), y `--debug` en cada comando hoja.
- **Multi‑contexto** con un único `~/.config/ddctl/config.yaml`, intercambiable con `--context`. Las variables de entorno (`DD_SITE`, `DD_API_KEY`, `DD_APP_KEY`) tienen prioridad.
- **Ayuda bilingüe.** Configura `DDOGCTL_LANG=es` o `DDOGCTL_LANG=en`.
- **Easter egg.** `ddogctl guaf` imprime el logo ASCII de Datadog.

> Como `kubectl get pods` pero para Datadog, con JSON listo para agentes incluido.

---

## Agentes de Claude / salida amigable para IA

`ddogctl` es de **primera clase para tool‑use con LLMs** (agentes de Claude, Claude Code, asistentes propios):

- **JSON compacto** con `--json`: `{cmd, n, data, meta}`, una sola línea, sin espacios y con nombres de campos estables.
- **Whitelist por defecto**: cada comando publica un conjunto de campos curado para la pregunta que responde (mira `DEFAULT_FIELDS` en `ddctl/normalize.py`).
- **Escape**: añade `--full` cuando realmente necesites el payload crudo de Datadog.
- **Errores también estructurados**: `{cmd, error:{type, message, status, payload}}` — los agentes pueden bifurcar por `status` sin tener que parsear texto.
- **Ahorro de tokens**: en benchmarks internos de triage SRE, los payloads de `ddogctl --json` resultaron **hasta ~88% más pequeños** que las mismas consultas vía el MCP oficial de Datadog, reduciendo costo y latencia.

Definición mínima de tool para Claude (Anthropic Tools API / Claude Code):

```json
{
  "name": "ddogctl_service_troubleshoot",
  "description": "Triage Datadog de un servicio en un solo paso (tasa de error, p95, top recursos con error, logs recientes).",
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

Luego, desde el agente:

```bash
ddogctl --json --context prd service troubleshoot \
  --service "$SERVICE" --env "$ENV" --from "$FROM"
```

Hay un recetario completo de triage en [`examples/triage-with-json.md`](examples/triage-with-json.md).

---

## Inicio rápido

```bash
pip install -e .

# Credenciales (PowerShell)
$env:DD_SITE   = "datadoghq.com"
$env:DD_API_KEY = "<TU_API_KEY>"
$env:DD_APP_KEY = "<TU_APP_KEY>"

ddogctl auth status
ddogctl monitors list --states "Alert,Warn" --page-size 50
ddogctl service troubleshoot --service checkout --env prd --from now-15m
ddogctl --json apm errors rate --service checkout --env prd --from now-1h --group-by resource_name
```

> macOS / Linux: cambia `$env:VAR = "..."` por `export VAR=...`.

---

## Autenticación

Las credenciales se resuelven en este orden:

1. **Variables de entorno**: `DD_SITE`, `DD_API_KEY`, `DD_APP_KEY`.
2. **YAML**: `~/.config/ddctl/config.yaml`.

```yaml
contexts:
  prd:
    site: datadoghq.com
    api_key: "TU_API_KEY"
    app_key: "TU_APP_KEY"
  staging:
    site: datadoghq.eu
    api_key: "..."
    app_key: "..."
```

Selecciona el contexto con `--context staging` y sobreescribe la ruta con `--config ./otro.yaml`.
La mayoría de endpoints (monitors, dashboards, incidents, synthetics, logs, APM, RUM, métricas)
**requieren Application Key** además de API Key.

---

## Opciones globales

| Opción | Descripción |
|---|---|
| `--context <nombre>` | Contexto YAML a usar. |
| `--config <ruta>`    | Ruta al archivo YAML. |
| `--json`, `-j`       | Emite JSON compacto a stdout (suprime tablas). |
| `--full`             | Junto a `--json`, emite el payload crudo de Datadog (sin whitelist). |
| `DDOGCTL_LANG=es\|en` | Idioma de la ayuda (por defecto `es`). |
| `--debug` (por comando) | Debug verboso, sin secretos — disponible en todo comando hoja. |

---

## Referencia de comandos

> Todos los comandos soportan `--json`, `--full`, `--context`, `--config` y `--debug`.

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
ddogctl incidents create --title "Título" --severity SEV-2 \
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
Columnas: `timestamp`, `service`, `status`, `message` (truncado a 400 chars).
Tiempo: `now`, `now-15m`, `-15m`, `-1h`, `-2d`, e ISO datetimes.

### apm
```bash
# Spans
ddogctl apm spans list   --service my-svc --env prd --from now-15m --limit 50
ddogctl apm spans search --query "service:my-svc env:prd" --from now-1h --limit 50
ddogctl apm spans aggregate --query "service:my-svc" --group-by resource_name --from now-1h

# Análisis de errores
ddogctl apm errors top-resources --service my-svc --env prd --from now-1h --limit 10
ddogctl apm errors rate          --service my-svc --env prd --from now-1h --group-by resource_name

# Traces
ddogctl apm trace get        --trace-id <id>
ddogctl apm trace timeseries --query "service:my-svc env:prd" --from now-1h
```

Notas:
- Oculta automáticamente columnas vacías; `env` / `service` se mueven al título de la tabla cuando son constantes.
- Duraciones en milisegundos (`dur_ms`); el conteo APM se lee desde `attributes.compute.c0`.

### service troubleshoot
**El comando estrella para SRE** — tasa de error APM + latencia p95, top recursos con error y últimos logs de error en un solo registro:
```bash
ddogctl service troubleshoot \
  --service checkout --env prd --from now-1h \
  [--cluster nombre_cluster] [--debug]
```

### services (Software Catalog)
```bash
ddogctl services apply --file ./service.yaml
ddogctl services apply --service my-svc --schema-version v2.1 --env prd \
  --description "Servicio de checkout" --tag team:platform --tag app:web --tier critical
ddogctl services list                # tabla por defecto
ddogctl services get    --service my-svc
ddogctl services delete --service my-svc
```

### metrics (Métricas)
```bash
# Series temporales con sparkline
ddogctl metrics query \
  --query "avg:kubernetes.cpu.requests{cluster:my} by {kube_deployment}" \
  --from now-1h --rollup 120 --limit 20 --spark

# Cardinalidad de tags
ddogctl metrics tag-cardinality --metric kubernetes.cpu.requests

# Capacidad Kubernetes (CPU/Memoria) por servicio o deployment
ddogctl metrics k8s-resources \
  --cluster your_cluster_name \
  --kube-service your_service_name \
  --from now-30m --rollup 120 [--cpu-unit mcores] [--debug]
```
CPU se imprime en cores o mCores (sin notación científica); la memoria se auto‑escala (B / KiB / MiB / GiB).

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
ddogctl hosts mute   --host <nombre> [--message "patching"] [--end <epoch>] [--override]
ddogctl hosts unmute --host <nombre>
```

### downtimes
```bash
ddogctl downtimes list [--current-only]
ddogctl downtimes schedule --scope "env:prd" \
  --start 2026-05-12T00:00:00Z --end 2026-05-12T01:00:00Z --message "Release"
ddogctl downtimes cancel --id <id>
```

### url (deep links, sin llamadas a la API)
Genera enlaces listos para compartir a la UI de Datadog sin gastar una llamada API:
```bash
ddogctl url trace     --trace-id <id> --from now-1h --to now
ddogctl url explorer  --query "service:my-svc env:prd" --from now-24h
ddogctl url service   --service my-svc --env prd --from now-24h
ddogctl url logs      --query "status:error service:my-svc" --from now-1h
ddogctl url monitor   --id 42
ddogctl url dashboard --id abc-123
ddogctl url incident  --id 17
```
El host base se infiere del `site` del contexto activo (o `--base`).

---

## Recetas

**Triage de producción en 90 segundos**
```bash
SVC=my-api; ENV=prd

ddogctl --json --context prd service troubleshoot --service "$SVC" --env "$ENV" --from -15m
ddogctl --json --context prd monitors list --tags "service:$SVC" --states "Alert,Warn"
ddogctl --json --context prd apm errors rate --service "$SVC" --env "$ENV" \
  --group-by resource_name --from -15m --limit 5 | jq '.data[0:3]'
```

**Compartir un deep link**
```bash
ddogctl url logs --query "service:$SVC status:error" --from now-1h
```

**Pipe a `jq`**
```bash
ddogctl --json apm errors top-resources --service "$SVC" --env "$ENV" --from now-1h --limit 5 \
  | jq '.data[] | "\(.count)\t\(.resource)"'
```

Un recorrido completo de triage está en [`examples/triage-with-json.md`](examples/triage-with-json.md).

---

## Contribuir

¡PRs e issues son bienvenidos! — especialmente:

- Nuevos comandos o flags que mapeen limpiamente a endpoints de Datadog.
- Shapes de respuesta token‑eficientes para agentes de IA.
- Ayuda bilingüe (el proyecto ya soporta strings completos ES/EN vía `ddctl/i18n.py`).
- Tests en `tests/` (corre con `pytest -q`).

```bash
pip install -e .
pip install pytest
pytest -q
```

Un test guardrail (`tests/test_debug_help.py`) garantiza que cada comando hoja expone `--debug`.

Lee [`CONTRIBUTING.md`](CONTRIBUTING.md) para la guía completa — arquitectura, cómo agregar un comando, contrato de salida, estilo y convenciones de commits/PRs.

---

## Licencia

Este proyecto está licenciado bajo la [Licencia MIT](LICENSE).

Si `ddogctl` te ahorra tiempo, considera darle una ⭐ al repo — ayuda mucho a que el proyecto llegue a otras personas que usen Datadog y construyan con IA.
