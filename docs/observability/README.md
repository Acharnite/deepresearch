# Observability

## Quick Start

Start Jaeger for trace visualization:

```bash
docker compose -f docs/observability/docker-compose.yaml up -d
```

Set the OTLP endpoint and run the app:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
python -m deepresearch.web.server
```

Open Jaeger UI at http://localhost:16686 to view traces.

## Spans

The following spans are created:

| Span Name | Location | Attributes |
|-----------|----------|------------|
| session.run | orchestrator.py | session.id, topic, budget |
| round.N | round_runner.py | session.id, round.num |
| scribe.compile | scribe_compiler.py | session.id, report.count |
| llm.MODEL | llm/client.py | model, tokens, cost |
| search.ENGINE | tools/web_search.py | engine, query |

## Exporters

- **Dev:** Console exporter (stdout)
- **Prod:** OTLP HTTP exporter (set OTEL_EXPORTER_OTLP_ENDPOINT)
