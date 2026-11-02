# Arquitectura

## Servicios

El sistema son cuatro servicios Python y un frontend estático, más el almacenamiento de AWS que comparten. Cada
servicio es un paquete Python independiente bajo `src/churn/`; el código que usa más de uno vive en
`src/churn/shared/`.

| Servicio | Paquete | Qué hace | Ciclo de vida | En AWS | En local (Docker Compose) |
|---|---|---|---|---|---|
| **Training** | `churn.training` | Features → XGBoost → nueva versión del modelo en S3 | Se ejecuta y termina, mensual o trimestralmente | Tarea programada de ECS Fargate | Contenedor `training`, se ejecuta con `up` y termina |
| **Scoring** | `churn.scoring` | Features → último modelo → scores actuales en DynamoDB + histórico en S3 | Se ejecuta y termina, mensualmente | Lambda (imagen de contenedor) con un schedule de EventBridge | Contenedor `scoring`, se ejecuta con `up` y termina |
| **Monitoring** | `churn.monitoring` | Predicciones pasadas frente a resultados reales → informe de rendimiento en S3 | Se ejecuta y termina, mensualmente después del scoring | Lambda o tarea programada de Fargate | Contenedor `monitoring`, se ejecuta con `up` y termina |
| **API** | `churn.api` | Sirve los scores y el informe de rendimiento por HTTP; expone métricas de Prometheus | Siempre activo | Lambda + API Gateway, o servicio de ECS | Contenedor `api`, permanece activo |
| **Frontend** | `frontend/` (HTML + JS) | Clientes en riesgo, detalle de cliente, rendimiento del modelo | Ficheros estáticos | S3 + CloudFront, `/api` enrutado a la API | Contenedor nginx `frontend`, `/api` redirigido por proxy a `api` |
| *(solo local)* Seed | `churn.localdev` | Crea el bucket + la tabla, escribe datos de origen sintéticos | Se ejecuta y termina | No se despliega: lo hacen Terraform y los sistemas de origen reales | Contenedor `seed`, se ejecuta primero con `up` |
| *(solo local)* Backtest | `churn.localdev.backtest` | Predicciones de meses pasados con modelos point-in-time | Se ejecuta y termina | No se despliega: en producción el histórico se acumula mes a mes | Contenedor `backtest`, antes de monitoring |

| Recurso de AWS | En local |
|---|---|
| Bucket de S3 `saas-churn-data` | Contenedor de LocalStack (`localstack`), solo S3 |
| Tabla de DynamoDB `ChurnPredictions` | Contenedor `amazon/dynamodb-local` (`dynamodb`) |
| Schedules de EventBridge | Tú: `docker compose run --rm --no-deps <job>` |
| CloudWatch / Amazon Managed Prometheus + Grafana | Contenedores `prometheus` y `grafana` |

### Quién lee y escribe qué

| Servicio | S3 | DynamoDB |
|---|---|---|
| Seed (local) | escribe `raw/` | crea la tabla |
| Training | lee `raw/`, escribe `features/`, `models/` | — |
| Scoring | lee `raw/` + `models/`, escribe `features/`, `predictions/` | **el único que escribe** |
| Backtest (local) | lee `raw/`, escribe `features/`, `predictions/` (meses pasados) | — |
| Monitoring | lee `raw/` + `predictions/`, escribe `features/`, `monitoring/` | — |
| API | lee `monitoring/model_performance.json` | solo lectura (`GetItem`, `Query`) |

Training, scoring, backtest y monitoring reconstruyen `features/` a partir de `raw/` antes de trabajar.
La reconstrucción es determinista, así que cada job es autónomo y puede ejecutarse por separado. Scoring necesita un modelo,
así que training debe haberse ejecutado al menos una vez; monitoring necesita predicciones cuyo resultado sea conocido.

### Observabilidad

- **Métricas de la API:** `GET /metrics` expone dos histogramas de Prometheus, `churn_api_request_duration_seconds`
  (por método, plantilla de ruta y estado) y `churn_api_dynamodb_duration_seconds` (por operación).
  Prometheus los recoge cada 5 s.
- **Métricas de las pruebas de carga:** k6 las envía a Prometheus mediante remote write: tasa de peticiones, iteraciones
  descartadas, latencia p50/p95/p99, usuarios virtuales y tasa de fallos.
- **Dashboard:** Grafana muestra ambas en un único dashboard, "Churn API & load tests"
  (`observability/grafana/dashboards/churn-api.json`), con los objetivos de 150 ms y 0.5% dibujados como líneas.
- **Cómo leerlo:** [ejecucion-local.md](ejecucion-local.md#cómo-leer-el-dashboard). Resultados y el análisis del cuello
  de botella de la API: [rendimiento.md](rendimiento.md).
- **La calidad del modelo** no está en Grafana: está en el frontend, a partir del informe de monitoring. Grafana es la
  vista operativa; el frontend es la vista de negocio (decisión D31).

## Flujo de datos

```
                         ┌──────────────────────────── S3 bucket: saas-churn-data ─────────────────────────────┐
 Sistemas origen         │                                                                                       │
 (hoy simulados          │  raw/                    features/                models/churn/       predictions/    │
  por el job seed)       │  crm_customers/          snapshot_month=YYYY-MM/  <version>/          snapshot_month= │
 ┌──────────────┐ingesta │  usage/                    features.parquet         model.json          YYYY-MM/      │
 │ CRM          ├───────►│  support_tickets/                                   metadata.json        predictions.  │
 │ Producto     │        │  billing_events/                                  latest.json          parquet       │
 │ Soporte      │        │       │                        ▲     │                 ▲     │               ▲         │
 │ Facturación  │        └───────┼────────────────────────┼─────┼─────────────────┼─────┼───────────────┼─────────┘
 └──────────────┘                │  cálculo de features   │     │  TRAINING       │     │  SCORING      │
                                 │  (training y scoring)  │     │                 │     │               │
                                 └────────────────────────┘     └─────────────────┘     └───────┬───────┘
                                                                                                │ un item por
                                                                                                ▼ cliente activo
                         Frontend / CRM ──HTTP──► API ──GetItem / Query──► DynamoDB: ChurnPredictions
```

Cada servicio es una función simple sobre S3 y DynamoDB, así que el mismo código se ejecuta en los emuladores, en moto
(tests) y en AWS. Solo cambian las variables de entorno de los endpoints (ver
[ejecucion-local.md](ejecucion-local.md#variables-de-entorno)).

## Estructura de S3

| Prefijo | Formato | Escrito por | ¿Se sobrescribe? |
|---|---|---|---|
| `raw/crm_customers/export_date=YYYY-MM-DD/part-0000.jsonl` | JSON Lines | Exportación del CRM (seed en local) | No, una partición por exportación; los lectores usan la más reciente |
| `raw/usage/month=YYYY-MM/part-0000.jsonl` | JSON Lines | Analítica de producto (seed en local) | No, una partición por mes |
| `raw/support_tickets/month=YYYY-MM/part-0000.jsonl` | JSON Lines | Soporte (seed en local) | No |
| `raw/billing_events/month=YYYY-MM/part-0000.jsonl` | JSON Lines | Facturación (seed en local) | No |
| `features/snapshot_month=YYYY-MM/features.parquet` | Parquet | Training y scoring | Sí, se reconstruye desde raw en cada ejecución (datos derivados) |
| `models/churn/<version>/model.json` | XGBoost JSON | Training | No, cada ejecución de training es una nueva versión |
| `models/churn/<version>/metadata.json` | JSON | Training | No |
| `models/churn/latest.json` | JSON `{"version": ...}` | Training | Sí, es el puntero que sigue scoring |
| `predictions/snapshot_month=YYYY-MM/predictions.parquet` | Parquet | Scoring (mes actual), backtest (meses pasados, local) | Sí para un mes que se vuelve a puntuar; si no, se acumula |
| `monitoring/model_performance.json` | JSON | Monitoring | Sí, cada ejecución reescribe el informe completo |

`<version>` es el timestamp UTC del training, por ejemplo `20261031T133725Z`. Los nombres de partición usan el estilo
`key=value` de Hive para que Athena o Glue puedan consultar más adelante los prefijos como tablas sin mover datos.

## DynamoDB: `ChurnPredictions`

Un item por cliente **actualmente activo**, que guarda solo el último score.

| Atributo | Tipo | Ejemplo | Notas |
|---|---|---|---|
| `CustomerID` | S (partition key) | `C-01412` | |
| `churn_probability` | N | `0.9855` | Probabilidad de cancelar dentro de `horizon_months` |
| `risk_band` | S | `high` | `high` ≥ 0.30, `medium` ≥ 0.10, si no `low` |
| `top_factors` | L de M | `[{"feature": "seats", "value": 156, "impact": 1.6135}, ...]` | Hasta 3 features que aumentan el riesgo (SHAP, log-odds) |
| `snapshot_month` | S | `2026-10` | Fecha de corte de los datos: las features usan los meses anteriores a este |
| `horizon_months` | N | `3` | |
| `model_version` | S | `20261031T133725Z` | Enlaza con `models/churn/<version>/` |
| `scored_at` | S | `2026-10-31T13:38:02Z` | |

**GSI `RiskBandIndex`**: partition key `risk_band`, sort key `churn_probability`, proyección ALL. Responde a
"clientes de alto riesgo, del peor al mejor" con un `Query` en lugar de un `Scan` completo de la tabla.

Cada ejecución de scoring sobrescribe los items de los clientes que puntúa y después borra los items cuyo
`snapshot_month` es distinto del mes de la ejecución (clientes que han cancelado desde la ejecución anterior), así que una
consulta nunca devuelve un score obsoleto. El histórico vive en `predictions/` en S3, no en DynamoDB.

## API

| Endpoint | Respaldado por | Respuesta |
|---|---|---|
| `GET /health` | — | `{"status": "ok"}` |
| `GET /customers/{customer_id}/churn` | `GetItem` | El item de DynamoDB, o 404 cuando el cliente no tiene un score activo |
| `GET /churn/at-risk?band=high&limit=50` | `Query` sobre `RiskBandIndex`, descendente | `{"band": ..., "customers": [...]}` |
| `GET /churn/summary` | `Query` con `Select=COUNT` por banda sobre `RiskBandIndex` | `{"customers": 889, "by_band": {"high": ..., "medium": ..., "low": ...}}` |
| `GET /model/performance` | `GetObject` de S3 sobre `monitoring/model_performance.json` | El informe de monitoring, o 404 antes de que se haya ejecutado monitoring |
| `GET /metrics` | — | Métricas de Prometheus (no aparecen en la documentación OpenAPI) |

La API es de solo lectura. Nunca calcula scores. El frontend accede a ella como `/api/...` a través de nginx.

### Informe de monitoring

```json
{
  "generated_at": "2026-10-31T16:02:11Z",
  "horizon_months": 3,
  "months": [
    {"snapshot_month": "2026-01", "model_version": "backtest-2026-01", "backtest": true,
     "customers_predicted": 840, "rows": 840, "positives": 125, "base_rate": 0.1488,
     "roc_auc": 0.91, "pr_auc": 0.62, "brier": 0.07, "top_decile_precision": 0.65, "top_decile_lift": 4.4,
     "high_band": {"flagged": 150, "precision": 0.5, "recall": 0.7},
     "medium_or_high_band": {"flagged": 280, "precision": 0.35, "recall": 0.9}}
  ],
  "pending_months": ["2026-10"]
}
```

`months` contiene todos los meses cuyo resultado es conocido; `pending_months` contiene los meses que aún están dentro de su
ventana de etiqueta de 3 meses. Los números de arriba son ilustrativos; [modelo.md](modelo.md#resultados-de-la-monitorización) tiene
los reales.

## Mapa del código

Todo el código Python vive bajo `src/`. La raíz del repositorio solo contiene los README, la documentación, Docker Compose, el
frontend, la configuración de observabilidad y la prueba de carga.

```
src/
  pyproject.toml           paquete, extras de dependencias por servicio (api, jobs, dev), configuración de pytest
  Dockerfile               dos imágenes: target `api` y target `jobs`
  churn/
    shared/                usado por más de un servicio
      config.py            env → Settings (región, bucket, tabla, horizonte); MODEL_PREFIX, RISK_BAND_INDEX
      aws.py               clientes de boto3 con la configuración compartida del pool de conexiones (solo boto3)
      s3_io.py             helpers de S3: JSON Lines, Parquet, JSON (pandas)
      raw_data.py          contrato de raw/: claves particionadas, escribir un batch, leer una fuente
      features.py          raw → snapshots (features + etiqueta), escribir/leer features/
      model_store.py       publicar una versión del modelo / cargar la última (XGBoost)
      predictions_store.py histórico de predicciones en predictions/ (escribir un mes, listar, cargar)
      metrics.py           asignación de banda de riesgo y métricas de evaluación (training y monitoring)
    training/
      train.py             split temporal, XGBoost, evaluación, baseline de regresión logística
      job.py               punto de entrada: features → entrenar → publicar  (python -m churn.training)
    scoring/
      score.py             puntuar, top factores SHAP, escribir en DynamoDB, borrar items obsoletos, histórico
      job.py               punto de entrada: features → scoring              (python -m churn.scoring)
    monitoring/
      monitor.py           cruzar las predicciones de un mes con los resultados reales, evaluar o marcar como pendiente
      job.py               punto de entrada: features → informe en S3        (python -m churn.monitoring)
    api/
      lookup.py            lecturas de DynamoDB (GetItem, query sobre el GSI, recuentos) y el informe de S3
      metrics.py           histogramas de Prometheus, /metrics con varios workers
      app.py               app FastAPI                                       (python -m churn.api)
    localdev/              solo local, nunca se despliega
      synthetic.py         simulador de clientes
      setup_resources.py   crear el bucket y la tabla (en AWS lo hace Terraform)
      seed.py              punto de entrada: recursos + datos sintéticos     (python -m churn.localdev)
      backtest.py          predicciones pasadas, modelos point-in-time       (python -m churn.localdev.backtest)
      args.py              argumentos del job seed (compartidos con la CLI)
    cli.py                 `churn <command>`: ejecuta cualquier servicio desde el venv, sin Docker
  tests/                   leakage, etiquetas, corte del backtest, monitoring, end-to-end sobre moto
  infra/main.tf            bucket de S3 + tabla de DynamoDB (con el GSI) en AWS
frontend/
  public/                  index.html, app.js, styles.css (sin paso de build, sin librerías externas)
  nginx/default.conf       sirve public/, redirige /api/ a la API por proxy
observability/
  prometheus.yml           configuración de scrape para la API
  grafana/                 datasource aprovisionado y el dashboard "Churn API & load tests"
loadtest/k6.js             prueba de carga de la ruta de lectura: 500 req/s constantes, o una rampa para encontrar la saturación
docker-compose.yml         emuladores, jobs, api, frontend, prometheus, grafana (+ loadtest bajo demanda)
```

La imagen de jobs también ejecuta monitoring y el backtest; la imagen de la API incorporó `prometheus-client`.

### Dependencias e imágenes

`pyproject.toml` instala solo boto3 por defecto; cada imagen añade el extra que necesita.

| Imagen | Extra | Contiene | Tamaño | Ejecuta |
|---|---|---|---|---|
| `churn-api` (target `api`) | `churn[api]` | boto3, FastAPI, uvicorn | 256 MB | `api` |
| `churn-jobs` (target `jobs`) | `churn[jobs]` | + pandas, NumPy, pyarrow, XGBoost, scikit-learn | 1.74 GB | `seed`, `training`, `scoring` |

La API nunca importa pandas ni XGBoost (`shared/aws.py` y `shared/config.py` dependen solo de boto3),
lo que mantiene su imagen pequeña y sus cold starts de Lambda rápidos. La mayor parte de la imagen de jobs son las librerías
opcionales de NVIDIA de XGBoost en Linux; está muy por debajo del límite de 10 GB de imagen de Lambda, y cambiar al
paquete `xgboost-cpu`, solo para CPU, la reduciría si hiciera falta.
