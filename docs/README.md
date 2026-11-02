# Documentación


Plataforma de predicción de churn para un negocio SaaS: los datos de origen llegan a S3, a partir de ellos se construyen
snapshots mensuales de features, se entrena un modelo XGBoost con snapshots pasados, se puntúa en batch a cada cliente activo,
y el último score de cada cliente se sirve desde DynamoDB a través de una pequeña API HTTP.

Son cuatro servicios y un frontend:

- **training:** features → XGBoost → versión del modelo en S3.
- **scoring:** último modelo → scores actuales en DynamoDB, cada mes.
- **monitoring:** predicciones pasadas frente a resultados reales → informe de rendimiento.
- **API:** lee DynamoDB y el informe; expone métricas de Prometheus.
- **frontend:** página estática con los clientes en riesgo, el detalle de cada cliente y el rendimiento del modelo.

En local, cada uno es un contenedor de Docker Compose, junto a LocalStack (S3), dynamodb-local (DynamoDB)
y Prometheus + Grafana para los dashboards de la API y de las pruebas de carga.

Todavía no hay datos reales de clientes, así que un simulador genera datos sintéticos realistas y los escribe
en S3 con la misma forma que tendrían los de los sistemas de origen reales.

## Orden de lectura

| Documento | Qué cubre |
|---|---|
| [arquitectura.md](arquitectura.md) | Componentes, flujo de datos, estructura en S3, esquema de DynamoDB, API, mapa del código |
| [decisiones.md](decisiones.md) | Cada decisión de diseño con su razonamiento y las alternativas descartadas |
| [datos-sinteticos.md](datos-sinteticos.md) | Cómo se comportan los clientes ficticios, esquemas de los registros raw, qué no es realista |
| [features-y-etiquetas.md](features-y-etiquetas.md) | Definición del snapshot, cada feature, la etiqueta, cómo se evita el leakage |
| [modelo.md](modelo.md) | Procedimiento de entrenamiento, evaluación, resultados, cómo interpretar las métricas |
| [ejecucion-local.md](ejecucion-local.md) | Instalación, comandos, tests, API, cómo ejecutar pruebas de carga e interpretar Grafana |
| [rendimiento.md](rendimiento.md) | Resultados de las pruebas de carga, punto de saturación, por qué se satura la API y cómo solucionarlo |
| [produccion.md](produccion.md) | Lo que aún falta para ejecutar esto en AWS real con datos reales |
| [estado-del-enunciado.md](estado-del-enunciado.md) | Cada punto de la especificación original, su estado y el trabajo pendiente |

## Resumen en un párrafo

`docker compose up` (o `churn run-all`) crea el bucket y la tabla, simula 2,000 clientes durante 24 meses (55,592 registros
raw en S3), construye 16,109 snapshots cliente-mes, entrena XGBoost (ROC AUC de test 0.916, frente a
0.888 de un baseline de regresión logística) y escribe un item por cliente activo (889) en DynamoDB.
`GET /customers/{id}/churn` devuelve ese item: probabilidad de cancelar en los próximos 3 meses, banda de riesgo,
las tres features que más empujan el riesgo al alza y la versión del modelo que lo generó.

## Sobre "agentic"

El scaffold original importaba un paquete llamado `agentic` con `Model().train()` y `load_model()`.
No existe ninguna librería con esa API; era un marcador de posición. La predicción de churn es una clasificación tabular
sobre el propio histórico de la empresa, así que se resuelve con XGBoost. Un LLM o un agente (por ejemplo Bedrock) no
forma parte de este sistema. Podría añadirse más adelante como una capa que explique los scores o sugiera
acciones de retención, pero no para calcular el score. Ver la decisión D1 en [decisiones.md](decisiones.md).
