# ddogctl

CLI simple para Datadog inspirado en kubectl, construida con Typer y Rich.

## Requisitos
- Python 3.10+

## Instalación (modo editable)

```bash
pip install -e .
```

## Variables de entorno
- `DD_SITE` (p. ej. `datadoghq.com`, `datadoghq.eu`, `us3.datadoghq.com`, etc.)
- `DD_API_KEY`
- `DD_APP_KEY`

Las credenciales se leen primero desde variables de entorno. Si no están presentes, se buscan en un archivo YAML por contexto.

### Configuración rápida (Windows PowerShell)

Temporal (solo sesión actual):
```powershell
$env:DD_SITE = "datadoghq.com"   # cámbialo si tu cuenta usa otra región
$env:DD_API_KEY = "<TU_API_KEY>"
$env:DD_APP_KEY = "<TU_APP_KEY>" # necesario para la mayoría de endpoints v1/v2
```

Persistente (requiere abrir nueva terminal):
```powershell
setx DD_SITE "datadoghq.com"
setx DD_API_KEY "<TU_API_KEY>"
setx DD_APP_KEY "<TU_APP_KEY>"
```

Verificación:
```powershell
ddogctl auth status
```

## Archivo de configuración
Ruta por defecto: `~/.config/ddctl/config.yaml`

Formato:
```yaml
contexts:
  prd:
    site: datadoghq.com
    api_key: "..."
    app_key: "..."
```

Puedes seleccionar el contexto con `--context <name>` y cambiar la ruta con `--config <path>`.

### Application Key (recomendado)
- Muchos endpoints (monitors, dashboards, incidents, synthetics, logs) requieren `DD_APP_KEY` además de `DD_API_KEY`.
- Crea un Application Key en Datadog y asígnale permisos adecuados.
- Ejemplo (PowerShell):
```powershell
$env:DD_APP_KEY = "<TU_APP_KEY>"
# o persistente
setx DD_APP_KEY "<TU_APP_KEY>"
```

Permisos sugeridos (dependiendo de lo que uses):
- Monitors: lectura/escritura de monitors
- Dashboards: lectura de dashboards
- Incidents: lectura/escritura de incidents
- Synthetics: lectura/ejecución de tests
- Logs: lectura de datos de logs (p. ej., logs_read_data o equivalente en tu plan)

## Uso
Opciones globales:
- `--context <nombre>`: Contexto a usar del archivo de configuración.
- `--config <ruta>`: Ruta al archivo de configuración YAML.
- `DDOGCTL_LANG=en|es`: Idioma de los mensajes de ayuda (por defecto `es`).

### Autenticación
```bash
ddogctl auth status
```
Imprime el `site` y `api_key_valid` tras consultar `GET /api/v1/validate`.

### Monitors
- Listar:
```bash
ddogctl monitors list [--name <substring>]
```
Muestra tabla con `id`, `name`, `type`, `state`.

- Silenciar:
```bash
ddogctl monitors mute --id <int>
```
Ejecuta `POST /api/v1/monitor/{id}/mute` y muestra el JSON resultante.

### Dashboards
```bash
ddogctl dashboards get --id <str>
```
Obtiene `GET /api/v1/dashboard/{id}` y muestra el JSON.

### Incidents
```bash
ddogctl incidents create --title "Título" --severity SEV-2
```
Crea `POST /api/v2/incidents` con el JSON requerido y muestra la respuesta.

### Synthetics
```bash
ddogctl synthetics trigger --public-id <id> --public-id <id2> ...
```
Realiza `POST /api/v1/synthetics/tests/trigger` con `{"tests":[{"public_id":"..."}]}` y muestra la respuesta.

### Logs
```bash
ddogctl logs query --from -1h --to now [--service payments] [--query "status:error"] [--limit 50]
```
Realiza `POST /api/v2/logs/events/search`. Acepta tiempos relativos `-15m`, `-1h`, `-2d` o datetime ISO. Construye la query incluyendo `service:<svc>` si se especifica, más `--query` o `"*"` por defecto. Muestra tabla con `timestamp`, `service`, `status`, `message` (truncado a 400 chars).

## Ejemplos
```bash
ddogctl auth status
ddogctl monitors list --name cpu
ddogctl monitors mute --id 12345
ddogctl dashboards get --id abc-def-123
ddogctl incidents create --title "Base de datos caída" --severity SEV-2
ddogctl synthetics trigger --public-id abcd123 --public-id efgh456
ddogctl logs query --from -15m --service checkout --query "status:error" --limit 100
ddogctl apm spans list --service payments --from now-15m
ddogctl apm spans search --query "service:payments env:prd" --from now-1h --limit 50
ddogctl apm errors top-resources --service payments --from now-24h
ddogctl apm errors rate --service payments --group-by resource_name --from now-1h
```

Filtrar por entorno (env):
```bash
ddogctl apm spans list --service my-service --env prd --from now-15m
ddogctl apm spans search --query "service:my-service" --env dev --from now-1h
```

### Services (Service Definitions)
Crear/actualizar desde YAML:
```bash
ddogctl services apply --file .\service.yaml
```

Crear/actualizar con flags mínimos:
```bash
ddogctl services apply `
  --service my-service `
  --schema-version v2.1 `
  --env prd `
  --description "Servicio de checkout" `
  --tag team:platform --tag app:web --tier critical
```

Listar definiciones (tabla por defecto; `--debug` muestra JSON crudo):
```bash
ddogctl services list
ddogctl services list --debug
```

Obtener una definición por nombre:
```bash
ddogctl services get --service my-service
```

Eliminar una definición:
```bash
ddogctl services delete --service my-service
```

API de referencia: https://docs.datadoghq.com/es/api/latest/service-definition

### Métricas

Consultar series temporales:
```bash
ddogctl metrics query `
  --query "avg:kubernetes.cpu.requests{cluster:tor-prod-rke2} by {kube_deployment}" `
  --from now-1h --rollup 120 --limit 20 --spark
```
Opciones:
- `--limit`: máximo de series a mostrar
- `--spark` y `--spark-points`: muestra un mini‑gráfico por serie
- `--scope-tag kube_deployment`: conserva solo ese tag del scope en la tabla
- Los valores se imprimen con decimales fijos (sin notación científica)

Cardinalidad de tags:
```bash
ddogctl metrics tag-cardinality --metric kubernetes.cpu.requests
```

Recursos de Kubernetes (CPU/Memoria) por servicio o deployment:
```bash
ddogctl metrics k8s-resources `
  --cluster tor-prod-rke2 `
  --kube-service tap-ui-prd `
  --from now-30m --rollup 120 `
  [--cpu-unit mcores] [--debug]
```
Muestra el último punto del rango seleccionado:
- CPU requests: `sum:kubernetes.cpu.requests{...}`
- CPU limits: `sum:kubernetes.cpu.limits{...}`
- CPU usage: `sum:kubernetes.cpu.usage.total{...}.as_rate()` y se convierte de nanocores/seg a cores; si pasas `--cpu-unit mcores` se muestra en millicores
- Memoria requests: `sum:kubernetes.memory.requests{...}`
- Memoria limits: `sum:kubernetes.memory.limits{...}`
- Memoria usage: `sum:container.memory.usage{...}` con unidades legibles (B/KiB/MiB/GiB)

Notas:
- Todas las agregaciones usan `sum` para representar el total del workload seleccionado.
- Los números de CPU evitan notación científica; la memoria se auto‑escala a unidades humanas.
