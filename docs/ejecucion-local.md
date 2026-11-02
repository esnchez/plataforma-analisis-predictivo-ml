# Ejecución en local

Hay dos formas de ejecutar el sistema en local:

- **Docker Compose (recomendado):** cada servicio en su propio contenedor, como en AWS. Solo hace falta
  Docker.
- **venv de Python:** los mismos servicios como procesos normales en tu máquina, con el comando `churn`.
  Es más rápido para iterar sobre el código, y es necesario para los tests.

Ambas usan los mismos emuladores: LocalStack para S3 y dynamodb-local para DynamoDB.

## Docker Compose

### Qué se ejecuta

| Contenedor | Hace el papel de | Ciclo de vida |
|---|---|---|
| `localstack` | S3 | Permanece activo (puerto 4566) |
| `dynamodb` | DynamoDB | Permanece activo (puerto 8001 en tu máquina). Los datos están en memoria y se pierden al reiniciar |
| `seed` | Terraform + los sistemas de origen reales | Se ejecuta una vez: crea el bucket y la tabla, escribe datos sintéticos y termina |
| `training` | Tarea programada de ECS Fargate | Se ejecuta una vez después de `seed`: entrena, publica una versión del modelo y termina |
| `scoring` | Lambda mensual | Se ejecuta una vez después de `training`: escribe los scores en DynamoDB y termina |
| `backtest` | — (solo local) | Se ejecuta una vez después de `scoring`: predicciones de meses pasados con modelos point-in-time, y termina |
| `monitoring` | Tarea programada mensual | Se ejecuta una vez después de `backtest`: evalúa las predicciones pasadas, escribe el informe y termina |
| `api` | Lambda + API Gateway / servicio de ECS | Arranca cuando `scoring` termina con éxito y permanece activo (puerto 8000) |
| `frontend` | S3 + CloudFront | nginx, permanece activo (puerto 8080); `/api` se redirige por proxy a `api` |
| `prometheus` | Amazon Managed Prometheus / CloudWatch | Permanece activo (puerto 9090); recoge las métricas de la API y recibe las métricas de k6 |
| `grafana` | Amazon Managed Grafana | Permanece activo (puerto 3000), sin login; dashboard "Churn API & load tests" |
| `loadtest` | k6 | Solo cuando lo ejecutas explícitamente |

| Abre | Qué ves |
|---|---|
| http://localhost:8080 | Frontend: recuentos por banda, clientes en riesgo, detalle de cliente, rendimiento del modelo por mes |
| http://localhost:3000 | Grafana: tasa de peticiones y latencia de la API, tiempo de DynamoDB, métricas de k6 durante las pruebas de carga |
| http://localhost:8000/docs | API, interactiva |
| http://localhost:9090 | Prometheus, para consultas ad hoc |

El gráfico de rendimiento del modelo del frontend necesita que el job de monitoring haya terminado (unos 2–3 minutos
después de `up`, porque el backtest entrena siete modelos). Hasta entonces indica que todavía no existe ningún informe.

Los jobs se ejecutan al arrancar y después **terminan** en lugar de quedarse inactivos. Así se comportan en AWS:
una tarea programada o una Lambda arranca, trabaja y se detiene. También hace visible el éxito: un job que
ha funcionado muestra `Exited (0)`, uno que ha fallado muestra un código distinto de cero, y los servicios que van después no
arrancan. Ver la decisión D25.

### Comandos

```bash
docker compose up -d --build        # construye las imágenes, ejecuta todos los jobs en orden, arranca todo lo demás (~4 min)
docker compose ps -a                # jobs "Exited (0)"; localstack, dynamodb, api, frontend, prometheus, grafana "Up"
docker compose logs training        # métricas del modelo de esta ejecución
docker compose logs scoring         # clientes puntuados por banda de riesgo
docker compose logs monitoring      # precisión de la banda high por mes evaluado

curl http://localhost:8000/customers/C-01412/churn
curl "http://localhost:8000/churn/at-risk?band=high&limit=5"
# documentación interactiva de la API: http://localhost:8000/docs
```

Volver a ejecutar un job, el equivalente local de su schedule en AWS:

```bash
docker compose run --rm --no-deps scoring          # scoring mensual
docker compose run --rm --no-deps monitoring       # actualizar el informe de rendimiento del modelo
docker compose run --rm --no-deps training         # reentrenar; ejecuta scoring después para usar el nuevo modelo
docker compose run --rm --no-deps backtest         # recalcular las predicciones de los meses pasados

# Con argumentos, repite el comando completo: las palabras tras el nombre del servicio sustituyen a su comando.
docker compose run --rm --no-deps scoring python -m churn.scoring --snapshot-month 2026-09
docker compose run --rm --no-deps seed python -m churn.localdev --customers 5000
```

`--no-deps` es importante. Sin él, Compose también vuelve a ejecutar los jobs de los que depende este (`scoring`
volvería a ejecutar antes `seed` y `training`).

Después de cambiar código Python, reconstruye: `docker compose up -d --build` (o `docker compose build`).

Para pararlo todo: `docker compose down`. Los datos de DynamoDB están en memoria y los jobs lo reconstruyen todo en
el siguiente `up`.

### Consultar los emuladores desde tu máquina

```bash
aws --endpoint-url http://localhost:4566 s3 ls s3://saas-churn-data/ --recursive | head
aws --endpoint-url http://localhost:4566 s3 cp s3://saas-churn-data/models/churn/latest.json -
aws --endpoint-url http://localhost:8001 dynamodb get-item --table-name ChurnPredictions --key '{"CustomerID":{"S":"C-01412"}}'
```

Sirve cualquier credencial (`test` / `test`).

## venv de Python

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e "src[dev]"            # todos los servicios + herramientas de test, y el comando `churn`
```

Arranca solo los emuladores y después apunta el código a ellos:

```bash
docker compose up -d localstack dynamodb
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-west-2 \
       AWS_ENDPOINT_URL_S3=http://localhost:4566 AWS_ENDPOINT_URL_DYNAMODB=http://localhost:8001
```

PowerShell:

```powershell
$env:AWS_ACCESS_KEY_ID="test"; $env:AWS_SECRET_ACCESS_KEY="test"; $env:AWS_REGION="us-west-2"
$env:AWS_ENDPOINT_URL_S3="http://localhost:4566"; $env:AWS_ENDPOINT_URL_DYNAMODB="http://localhost:8001"
```

Cada servicio tiene su propio punto de entrada (los mismos comandos que ejecutan los contenedores):

```bash
python -m churn.localdev     # seed
python -m churn.training
python -m churn.scoring      # --snapshot-month YYYY-MM
python -m churn.api          # API_HOST, API_PORT, API_WORKERS
```

O el atajo `churn`:

```bash
churn run-all                # seed → train → score
churn seed | train | score | serve
churn lookup C-01412
churn at-risk --band high --limit 10
```

No ejecutes `churn serve` y el contenedor `api` a la vez: ambos usan el puerto 8000.

## Variables de entorno

| Variable | Compose (dentro de los contenedores) | venv en tu máquina | En AWS | Valor por defecto |
|---|---|---|---|---|
| `AWS_ENDPOINT_URL_S3` | `http://localstack:4566` | `http://localhost:4566` | sin definir | sin definir |
| `AWS_ENDPOINT_URL_DYNAMODB` | `http://dynamodb:8000` | `http://localhost:8001` | sin definir | sin definir |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | `test` / `test` | `test` / `test` | rol de IAM | — |
| `AWS_REGION` | `us-west-2` | `us-west-2` | tu región | `us-west-2` |
| `CHURN_BUCKET` | | | | `saas-churn-data` |
| `CHURN_TABLE` | | | | `ChurnPredictions` |
| `CHURN_HORIZON_MONTHS` | | | | `3` |
| `API_HOST` / `API_PORT` / `API_WORKERS` | `0.0.0.0` / `8000` / `4` | | | `127.0.0.1` / `8000` / `1` |

Las variables de endpoint las lee el propio boto3, así que el código no tiene ninguna configuración de endpoint. `AWS_ENDPOINT_URL`
(sin sufijo) sigue funcionando y apunta todos los servicios a un mismo sitio, por ejemplo un único LocalStack.

## Tests

```bash
pytest -q src/tests     # 5 tests, unos 30 segundos, sobre moto: no hacen falta Docker ni emuladores
```

| Test | Comprueba |
|---|---|
| `test_features_ignore_data_from_the_snapshot_month_onwards` | Que no haya leakage temporal en las features |
| `test_labels_follow_the_cancellation_window` | Ventana de etiqueta, meses recientes sin etiqueta, los clientes que cancelan salen de la población, warm-up |
| `test_backtest_only_learns_from_outcomes_known_before_the_month` | El corte point-in-time del backtest: un modelo para el mes M nunca ve un resultado de M en adelante |
| `test_monitoring_evaluates_known_outcomes_and_waits_for_the_rest` | Métricas de monitoring sobre un mes construido a mano, y "pending" mientras los resultados son desconocidos |
| `test_services_end_to_end_from_raw_s3_to_api` | Los puntos de entrada reales de los servicios en el orden de Compose: seed → training → scoring → backtest → monitoring → API. Comprueba las consultas a DynamoDB, el orden del GSI, el borrado de items obsoletos, el informe de rendimiento, los recuentos por banda, las etiquetas de `/metrics` y los códigos de estado de la API |

La primera ejecución después de instalar puede tardar unos minutos mientras Python compila las librerías.

## Prueba de carga y Grafana

Grafana solo muestra tráfico en directo: los jobs no le envían nada, y la API está inactiva salvo que el frontend o
una prueba de carga la llamen. **Un dashboard plano significa que no hay tráfico**, no que la configuración esté rota. Está pensado para
mirarlo mientras se ejecuta una prueba de carga.

1. Abre http://localhost:3000. El dashboard es "Churn API & load tests" y se refresca cada 5 s.
   Deja el selector de tiempo en "Last 15 minutes".
2. Lanza una prueba:

   ```bash
   # Rampa de 50 a 400 req/s durante 3 minutos: encuentra el punto de saturación
   SCENARIO=ramp MAX_RATE=400 RAMP_DURATION=3m docker compose --profile loadtest run --rm loadtest

   # Tasa constante: pasa/falla según los objetivos del README (por defecto 500 req/s durante 5 min)
   RATE=100 DURATION=60s docker compose --profile loadtest run --rm loadtest
   ```

   PowerShell: `$env:SCENARIO="ramp"; $env:MAX_RATE="400"; $env:RAMP_DURATION="3m"; docker compose --profile loadtest run --rm loadtest`
3. Observa cómo reaccionan los paneles. Para ver una ejecución anterior, amplía el selector de tiempo (por ejemplo "Last 1 hour").

k6 se ejecuta dentro de la red de Docker y llama a `http://api:8000/customers/{random id}/churn`, un único
`GetItem` de DynamoDB por petición. Los 404 (clientes que han cancelado) cuentan como respuestas válidas.

- **steady:** una tasa de llegada fija. Pasa o falla según los objetivos del README (p50 < 50 ms,
  p95 < 100 ms, p99 < 150 ms, errores < 0.5%).
- **ramp:** la tasa de llegada sube linealmente de 50 req/s a `MAX_RATE`. Informa sin
  pasa/falla.

### Cómo leer el dashboard

**Fila superior, medida dentro de la API:**

| Panel | Qué mirar |
|---|---|
| Requests per second by route | Cuánto tráfico está gestionando realmente la API |
| Customer lookup latency (API) | p50/p95/p99 por consulta. La línea roja discontinua es el objetivo de 150 ms |
| DynamoDB time inside requests | La parte de esa latencia que corresponde a la base de datos |
| Server errors (5xx share) | Debería mantenerse en 0%. El objetivo es menos de 0.5% |

**Fila inferior, medida por k6 (el cliente):**

| Panel | Qué mirar |
|---|---|
| Served vs dropped requests | "Dropped" por encima de 0: k6 no ha podido mantener la tasa objetivo porque las respuestas eran demasiado lentas, así que el sistema está saturado |
| Latency seen by k6 | Lo que experimentaría un usuario, una línea por escenario. Las líneas de una ejecución terminada se quedan planas durante unos minutos |
| Virtual users | Sube cuando las respuestas se ralentizan, porque k6 abre más conexiones para mantener la tasa |
| Failed requests | Timeouts y códigos de estado inesperados |

Cómo leer una prueba:

- **Sano:** served sigue al objetivo, dropped es 0 y p99 se mantiene por debajo de la línea discontinua.
- **Punto de saturación:** donde aparece dropped y p99 cruza la línea.
- **Dónde está el cuello de botella:** compara los dos paneles de latencia. Un tiempo de DynamoDB cercano a la latencia de la API
  significa que la base de datos es lenta. Una latencia de la API muy por encima del tiempo de DynamoDB significa que las peticiones están esperando dentro
  de la API.
- **Huecos en los paneles de la API:** la API sobrecargada no ha podido responder a tiempo al scrape de Prometheus.

Los resultados, el punto de saturación y el análisis de lo que limita la API en local están en
[rendimiento.md](rendimiento.md).
