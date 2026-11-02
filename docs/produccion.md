# Camino a producción

Lo que existe hoy funciona de extremo a extremo en local. Esta página enumera lo que aún falta para ejecutarlo en AWS
con datos reales, y cómo comprobar los objetivos de latencia contra DynamoDB real.

## Qué está listo

- Tres servicios con sus propios puntos de entrada, y dos imágenes de contenedor (`api`, `jobs`) construidas a partir de
  `src/Dockerfile` ([arquitectura.md](arquitectura.md#servicios)).
- Código que funciona sin cambios en AWS: allí simplemente no se definen las variables de endpoint.
- Terraform para la capa de datos (`src/infra/main.tf`): el bucket de S3 (cifrado, versionado, con acceso
  público bloqueado) y la tabla de DynamoDB con `RiskBandIndex` y recuperación point-in-time.
  `terraform validate` pasa. No se ha aplicado en ninguna cuenta.
- Contratos de datos en bruto para las cuatro fuentes ([datos-sinteticos.md](datos-sinteticos.md#esquemas-de-los-registros-en-bruto)).

## Datos reales

- Sustituir el job de seed por exportaciones reales en los mismos prefijos `raw/` y esquemas. Opciones:
  exportaciones programadas desde el CRM y la facturación (por ejemplo Salesforce, Stripe), eventos de analítica de producto
  a través de Kinesis Firehose hacia `raw/usage/`, y la API del servicio de soporte.
- **Conservar el histórico del CRM.** Exportar la tabla de clientes a diario en su propia partición, y cambiar
  `shared/features.py` para que tome el plan y las licencias según la fecha de cada snapshot, no de la última exportación.
- Agregar los eventos de producto en bruto en los campos mensuales de `usage` durante la construcción de features.
- Eliminar duplicados por ID de registro (`ticket_id`, `event_id`) durante la construcción de features, y usar IDs de
  batch únicos para la ingesta.
- Acordar con el negocio la definición de churn: qué cuenta como cancelación (¿bajar al plan
  gratuito? ¿impago?) y el horizonte. Ambos cambian las etiquetas.

## Cómputo

| Servicio | Imagen y comando | Servicio de AWS sugerido | Notas |
|---|---|---|---|
| Entrenamiento | `jobs`, `python -m churn.training` | Tarea de ECS Fargate, EventBridge Scheduler mensual o trimestral | Sin límite de tiempo. La memoria crece con clientes × meses; pandas sirve hasta unos pocos millones de filas, a partir de ahí conviene mover la construcción de features a Athena o Spark (Glue) |
| Scoring | `jobs`, `python -m churn.scoring` | Lambda (imagen de contenedor), EventBridge Scheduler el día 1 de cada mes | Segundos con este tamaño; el límite de 15 minutos de Lambda queda muy lejos. Fargate es la alternativa si crece |
| Monitorización | `jobs`, `python -m churn.monitoring` | Lambda (imagen de contenedor) o tarea de Fargate, EventBridge Scheduler después del scoring | Segundos. Su valor está en ejecutarse cada mes: el histórico de resultados reales se acumula solo (el backtest es solo local) |
| API | `api`, `python -m churn.api` | Lambda + API Gateway (con el adaptador Mangum), o servicio de ECS detrás de un ALB | Lambda para tráfico ligero o irregular; un servicio siempre activo es más barato con cientos de req/s sostenidas, ya que API Gateway cobra por petición. Corrige y vuelve a medir su CPU por petición antes de dimensionarlo (ver [rendimiento.md](rendimiento.md#por-qué-la-api-se-satura-pronto)) |
| Frontend | `frontend/public/` (ficheros estáticos) | Bucket de S3 + CloudFront; un segundo behavior de CloudFront envía `/api/*` a API Gateway o al ALB | La misma forma de origen único que el nginx local (decisión D32) |

Las imágenes van a ECR. Añade el cómputo a Terraform junto con:

- **IAM, un rol por servicio:**
  - Entrenamiento: `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` sobre el bucket.
  - Scoring: lo mismo, más `dynamodb:BatchWriteItem`, `dynamodb:Scan`, `dynamodb:DeleteItem` sobre la
    tabla.
  - Monitorización: `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` sobre el bucket.
  - API: `dynamodb:GetItem` y `dynamodb:Query` sobre la tabla y su índice, más `s3:GetObject` solo sobre
    `monitoring/model_performance.json`.
- **Autenticación en la API** (el proveedor de identidad del frontend, o autenticación IAM en API Gateway). Las
  puntuaciones son datos de negocio sensibles.
- **Orden:** el scoring necesita un modelo publicado, así que la primera ejecución de entrenamiento tiene que hacerse antes de la
  primera ejecución programada de scoring.

## Probar los objetivos de latencia contra DynamoDB real

Sí, se puede probar. Los emuladores locales no pueden hacerlo (ver
[rendimiento.md](rendimiento.md)), pero una prueba corta en una cuenta sandbox de AWS sí.
Cuesta poco y se elimina por completo después.

**La restricción clave: ejecuta k6 dentro de la misma región de AWS que la tabla.** Ejecutar k6 desde un portátil
mide internet: desde España a `us-west-2` solo el viaje de ida y vuelta ronda los 150 ms, así que p99 < 150 ms
fallaría por motivos ajenos al sistema. Incluso una región cercana añade al p99 la variabilidad de la red
doméstica.

### Pasos

1. **Crear la capa de datos.** Los nombres de bucket son globales entre todas las cuentas de AWS, así que `saas-churn-data`
   probablemente esté ocupado. Elige uno único:

   ```bash
   cd src/infra
   terraform init
   terraform apply -var bucket_name=saas-churn-data-<your-suffix> -var region=eu-west-1
   ```

2. **Llenarla**, desde tu máquina con credenciales reales y las variables de endpoint sin definir. El job de seed
   escribe datos sintéticos en el bucket real; encuentra el bucket y la tabla ya creados y
   no los toca.

   ```bash
   unset AWS_ENDPOINT_URL AWS_ENDPOINT_URL_S3 AWS_ENDPOINT_URL_DYNAMODB
   export AWS_REGION=eu-west-1 CHURN_BUCKET=saas-churn-data-<your-suffix>
   python -m churn.localdev && python -m churn.training && python -m churn.scoring
   ```

3. **Arrancar una instancia EC2 en la misma región** (por ejemplo `c7i.xlarge`, Amazon Linux con Docker),
   con un rol de instancia que permita `dynamodb:GetItem` y `dynamodb:Query` sobre la tabla. En ella, ejecuta
   la imagen de la API y k6 en paralelo:

   ```bash
   git clone <this repo> && cd plataforma-analisis-predictivo-ml
   docker build --target api -t churn-api src
   docker run -d --name api --network host -e AWS_REGION=eu-west-1 churn-api
   docker run --rm -i --network host -e BASE_URL=http://localhost:8000 grafana/k6:1.3.0 run - < loadtest/k6.js
   ```

   Esto mide la API más DynamoDB real, con solo la red dentro de la región entre ambos.
   `--network host` en la API es importante: con el límite de saltos por defecto de IMDSv2, que es 1, un contenedor en la red
   bridge de Docker no puede llegar a las credenciales del rol de instancia, y todas las peticiones fallarían.

4. **Opcional, fidelidad completa:** despliega la API tal como está previsto (Lambda + API Gateway o ECS + ALB) y
   apunta k6, en la misma instancia EC2, a su URL. Eso añade el gateway o el balanceador de carga, unos pocos ms,
   y los arranques en frío de Lambda.

5. **Eliminar todo:**
   - Termina la instancia.
   - Vacía el bucket. Está versionado, así que usa el botón "Empty" de la consola, que también borra las versiones
     antiguas; `aws s3 rm --recursive` las deja.
   - Ejecuta `terraform destroy` con los mismos valores de `-var`.

### Cuánto cuesta

Una ejecución de 5 minutos a 500 req/s son unas 150,000 lecturas. En una tabla on-demand eso cuesta una fracción de
céntimo. Una instancia EC2 durante una hora cuesta menos de un dólar, y el uso de S3 es despreciable. Las tablas on-demand
sirven miles de lecturas por segundo desde el principio, así que 500 req/s no necesita calentamiento ni planificación
de capacidad.

### Qué esperar

`GetItem` de DynamoDB en la misma región suele responder en pocos milisegundos (un solo dígito), y la API añade
unos 2 ms (medido en local). Un p99 por debajo de 150 ms a 500 req/s debería cumplirse con un margen amplio. Si
no es así, mira primero:

- el número de workers de la API (`API_WORKERS`);
- el tamaño de la instancia, ya que k6 y la API la comparten;
- los arranques en frío de Lambda, en la variante de fidelidad completa.

## Monitorización

- **Jobs:** éxito o fallo, y duración, mediante alarmas de CloudWatch sobre las tareas programadas.
- **API:**
  - El mismo dashboard de Grafana funciona en AWS con Amazon Managed Prometheus, recogiendo `/metrics`
    desde ECS (un sidecar con el colector ADOT) y recibiendo los remote writes de k6, con Amazon Managed Grafana.
  - En Lambda, `/metrics` no encaja (no hay un proceso de larga duración del que recoger métricas). Usa en su lugar las métricas
    de CloudWatch de API Gateway para la tasa de peticiones, la latencia y los 5xx.
  - En ambos casos, vigila también el throttling de DynamoDB.
- **Calidad del modelo:** el servicio de monitorización ya existe (decisión D28). Añade alertas sobre su informe,
  además de las comprobaciones de drift de [modelo.md](modelo.md#reentrenamiento-y-drift-pendiente-con-datos-reales). Consulta
  `predictions/` y `features/` con Athena; ya están particionados al estilo Hive.

## Trabajo opcional futuro

- Una capa de LLM (por ejemplo Claude en Bedrock con herramientas) que lea la puntuación de un cliente, sus factores principales
  y su historial de soporte, y redacte una explicación o un plan de retención. Consume las puntuaciones; no
  sustituye al modelo (decisión D1).
- Re-scoring bajo demanda cuando ocurran eventos importantes (decisión D17).
- Autenticación y vistas por usuario en el frontend.
