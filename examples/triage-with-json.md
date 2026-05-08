# Production triage flow with `ddogctl --json`

A short example of how to combine `ddogctl --json` with `jq` to perform a
fast, low-token-cost SRE triage of a backend service.

## Three calls cover most triage cases

```bash
SVC=my-api
ENV=prd

ddogctl --json --context prd service troubleshoot \
  --service "$SVC" --env "$ENV" --from -15m

ddogctl --json --context prd monitors list \
  --tags "service:$SVC" --states "Alert,Warn"

ddogctl --json --context prd apm errors rate \
  --service "$SVC" --env "$ENV" --group-by resource_name --from -15m --limit 5
```

`service troubleshoot` consolidates four internal Datadog calls (overview,
errors aggregate, top error resources, recent error logs) into a single
flat record under 1.5 KB.

## Sample shape

```json
{"cmd":"service.troubleshoot","n":1,"data":{
  "service":"my-api","env":"prd","window":"-15m..now",
  "err_rate_pct":0.34,"req_total":580,"req_err":2,
  "lat_ms":{"p50":23.93,"p95":3847.49,"p99":11558.91},
  "top_err_resources":[
    {"name":"POST /api/foo","count":1,"pct":50.0},
    {"name":"POST /api/bar","count":1,"pct":50.0}
  ],
  "top_err_msgs":[
    {"msg":"Validation error","count":3}
  ]
}}
```

## Common follow-ups

```bash
# Pull the worst resource only
ddogctl --json --context prd service troubleshoot \
  --service "$SVC" --env "$ENV" --from -15m \
  | jq '.data.top_err_resources[0]'

# Need the raw payload for deeper investigation
ddogctl --json --full --context prd apm spans search \
  --query "service:$SVC status:error" --from -15m --limit 3
```
