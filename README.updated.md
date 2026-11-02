# Plataforma de Análisis Predictivo para SaaS

> Versión actualizada del README según lo implementado. El enunciado original del proyecto se conserva sin cambios en [README.md](README.md).

**Problema:** Empresas SaaS no logran prever el churn de clientes, resultando en pérdida de ingresos y altos costos de adquisición de nuevos usuarios.

**Solución:** Plataforma que predice, para cada cliente activo, la probabilidad de cancelar en los próximos 3 meses, con un modelo XGBoost entrenado con el histórico propio de la empresa. Los datos de origen llegan a S3, el modelo puntúa a todos los clientes en batch y el último score de cada cliente se consulta en DynamoDB a través de una API.

**Resultado actual (datos sintéticos):** ROC AUC 0,916 y PR AUC 0,614 en meses de test no vistos (regresión logística de referencia: 0,888 / 0,548). El 10% de clientes con mayor score concentra 5,2 veces la tasa base de churn. Con datos reales se esperan métricas más bajas: ver [docs/modelo.md](docs/modelo.md).

**Tecnologías:** Python 3, XGBoost, pandas, scikit-learn, FastAPI, AWS S3, DynamoDB, Terraform, Docker Compose, LocalStack, dynamodb-local, moto, k6

> **Nota sobre "agentic":** la versión inicial del proyecto importaba un paquete `agentic` que no existe con esa API; era un marcador de posición. La predicción de churn es clasificación sobre datos tabulares y se resuelve con XGBoost entrenado con datos propios, no con un LLM. Ver la decisión D1 en [docs/decisiones.md](docs/decisiones.md).

## Documentación

Toda la documentación técnica está en [`docs/`](docs/README.md):

| Documento | Contenido |
|---|---|
| [arquitectura.md](docs/arquitectura.md) | Componentes, flujo de datos, estructura de S3, esquema de DynamoDB, API |
| [decisiones.md](docs/decisiones.md) | Cada decisión de diseño, su motivo y las alternativas descartadas |
| [datos-sinteticos.md](docs/datos-sinteticos.md) | Cómo se generan los datos sintéticos y sus limitaciones |
| [features-y-etiquetas.md](docs/features-y-etiquetas.md) | Snapshots, features, etiqueta y prevención de fugas de datos |
| [modelo.md](docs/modelo.md) | Entrenamiento, evaluación y resultados |
| [ejecucion-local.md](docs/ejecucion-local.md) | Puesta en marcha, comandos, tests, pruebas de carga y cómo leer Grafana |
| [produccion.md](docs/produccion.md) | Lo que falta para producción en AWS con datos reales |
| [rendimiento.md](docs/rendimiento.md) | Resultados de las pruebas de carga, punto de saturación y por qué se satura la API |
| [estado-del-enunciado.md](docs/estado-del-enunciado.md) | Estado de cada punto del enunciado original y trabajo pendiente |

## Arquitectura

```plaintext
 CRM / Producto / Soporte / Facturación
              │ ingesta (hoy: simulador)
              ▼
 S3 raw/ ──► features/ (snapshot mensual por cliente) ──► entrenamiento XGBoost ──► models/churn/<versión>/
                    │                                                                    │
                    └──────────────────────────► scoring batch ◄─────────────────────────┘
                                                      │
                                   ┌──────────────────┴──────────────────┐
                                   ▼                                     ▼
                     DynamoDB ChurnPredictions              S3 predictions/ (histórico)
                     (último score por cliente)
                                   │
                                   ▼
                       API (FastAPI) ──► Frontend
```

- **S3:** zona raw inmutable por fuente y periodo, features en Parquet, modelos versionados e histórico de predicciones.
- **Modelo:** XGBoost sobre 14 features de uso, soporte, facturación y contrato. Split temporal: entrena con el pasado y se evalúa con meses posteriores.
- **DynamoDB:** un ítem por cliente activo con probabilidad, banda de riesgo (`high`/`medium`/`low`), los 3 factores que más suben el riesgo (SHAP) y la versión del modelo. El índice `RiskBandIndex` lista los clientes de una banda ordenados por probabilidad.
- **API:** `GET /customers/{id}/churn` y `GET /churn/at-risk?band=high`.

## Servicios

| Servicio | Código | En AWS | En local (Docker Compose) |
|---|---|---|---|
| Entrenamiento | `src/churn/training/` | Tarea ECS Fargate programada (mensual o trimestral) | Contenedor `training`: se ejecuta al arrancar y termina |
| Scoring | `src/churn/scoring/` | Lambda mensual (imagen de contenedor); **único servicio que escribe en DynamoDB** | Contenedor `scoring`: se ejecuta tras el entrenamiento y termina |
| Monitorización | `src/churn/monitoring/` | Tarea programada mensual tras el scoring | Contenedor `monitoring`: compara predicciones pasadas con lo que ocurrió y termina |
| API | `src/churn/api/` | Lambda + API Gateway, o servicio ECS | Contenedor `api`: siempre activo en el puerto 8000; expone métricas Prometheus |
| Frontend | `frontend/` (HTML + JS) | S3 + CloudFront | Contenedor `frontend` (nginx) en el puerto 8080 |
| Seed (solo local) | `src/churn/localdev/` | — (Terraform y las fuentes reales) | Contenedor `seed`: crea recursos y datos sintéticos, y termina |
| Backtest (solo local) | `src/churn/localdev/backtest.py` | — (en producción el histórico se acumula mes a mes) | Contenedor `backtest`: genera predicciones de meses pasados con modelos de esa época |

El código común está en `src/churn/shared/`. S3 se emula con LocalStack y DynamoDB con `amazon/dynamodb-local`. Prometheus y Grafana recogen las métricas de la API y de las pruebas de carga.

## Inicio rápido (Docker Compose)

```bash
docker compose up -d --build        # seed → training → scoring → backtest → monitoring, en orden (~4 min)
docker compose ps -a                # los jobs terminan con "Exited (0)"

# http://localhost:8080   frontend: clientes en riesgo, detalle de cliente, rendimiento del modelo
# http://localhost:3000   Grafana: API y pruebas de carga (sin login)
# http://localhost:8000/docs   API

docker compose run --rm --no-deps scoring     # repetir el scoring mensual
docker compose run --rm --no-deps monitoring  # actualizar el informe de rendimiento
docker compose run --rm --no-deps training    # reentrenar
docker compose down
```

Para trabajar sin contenedores (entorno virtual de Python, comando `churn`), variables de entorno y comandos para PowerShell, ver [docs/ejecucion-local.md](docs/ejecucion-local.md).

## Despliegue a AWS

```bash
cd src/infra
terraform init
terraform apply        # bucket S3 + tabla DynamoDB con su índice
```

Terraform crea hoy la capa de datos. El cómputo (jobs batch y API), IAM y monitorización están descritos en [docs/produccion.md](docs/produccion.md) y aún no están implementados.

## Pruebas

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e "src[dev]"
pytest -q src/tests
```

Cinco tests sobre moto (sin Docker): ausencia de fugas temporales en las features, ventana de etiquetado, corte temporal del backtest, evaluación de la monitorización, y los servicios encadenados como en Compose (seed → training → scoring → backtest → monitoring → API).

## Pruebas de carga

```bash
docker compose --profile loadtest run --rm loadtest                    # k6, 500 req/s durante 5 minutos
SCENARIO=ramp MAX_RATE=400 docker compose --profile loadtest run --rm loadtest   # rampa para encontrar el punto de saturación
```

Los resultados se ven en directo en Grafana (http://localhost:3000).

| Métrica | Objetivo |
| --- | --- |
| Throughput | 500 req/s sostenidos |
| Latencia | p50 < 50 ms, p95 < 100 ms, p99 < 150 ms |
| Tasa de error | < 0,5% |

En local estos objetivos no se pueden verificar. El punto de saturación local está en torno a 80–100 req/s para p99 < 150 ms, y el máximo en ~190–200 req/s. Hay dos cuellos de botella: dynamodb-local (~45 ms por `GetItem`) y, por encima de ~120 req/s, la CPU de la propia API, que conviene perfilar antes de dimensionar producción. Hay que validar los objetivos contra DynamoDB real, con k6 en la misma región de AWS: el procedimiento está en [docs/produccion.md](docs/produccion.md#probar-los-objetivos-de-latencia-contra-dynamodb-real) y las mediciones locales en [docs/rendimiento.md](docs/rendimiento.md).

## Rendimiento real del modelo

El servicio de monitorización compara cada mes las predicciones con lo que ocurrió de verdad una vez cerrada su ventana de 3 meses. En el backtest (2026-01 a 2026-07), el ROC AUC se mantiene en 0,91–0,94 y la banda alta detecta ~73% de los clientes que cancelan. Su precisión baja de ~60% a ~49% cuando la tasa de churn cae de ~15% a ~12%, sin que el modelo empeore. Por eso un único "95% de precisión" no sirve para juzgar un modelo de churn. Detalle en [docs/modelo.md](docs/modelo.md#resultados-de-la-monitorización).

## Plan por fases

1. ~~Fase 1: definición del problema (horizonte de 3 meses, snapshots mensuales, features y etiqueta).~~
2. ~~Fase 2: prototipo en Python con XGBoost, datos sintéticos y tres servicios en Docker Compose (S3 en LocalStack, DynamoDB en dynamodb-local).~~
3. Fase 3: conexión a las fuentes de datos reales (CRM con histórico, eventos de producto, soporte, facturación) y reentrenamiento.
4. Fase 4: despliegue en AWS (jobs programados, API, frontend, IAM). Ya hechos en local: monitorización de precisión, dashboards de Grafana y frontend. Faltan las alertas y la deriva de datos.

## Takeaways

- Formular churn como clasificación con snapshots temporales y prevenir fugas de datos entre features y etiqueta.
- Entrenar y evaluar XGBoost con split temporal, comparándolo con un baseline lineal.
- Separar escritura batch (scoring mensual) de lectura online (DynamoDB por clave), con el histórico en S3.
- Medir el rendimiento real del modelo con un backtest sin fugas temporales, y leer la precisión junto a la tasa base.
- Instrumentar la API con Prometheus y visualizar pruebas de carga en Grafana para localizar el cuello de botella.
- Usar LocalStack, dynamodb-local y moto para desarrollar y probar el flujo AWS completo sin coste, y medir dónde está el cuello de botella antes de optimizar.
