# Decisiones de diseño

Cada entrada indica la decisión, por qué se tomó y qué se descartó. Si cambias una decisión, actualiza
su entrada en lugar de añadir otra que la contradiga.

## Modelado

### D1. Predecir el churn con XGBoost entrenado con nuestros propios datos, no con un LLM ni un framework "agéntico"

- **Decisión:** Árboles con gradient boosting (XGBoost) entrenados con los snapshots históricos de clientes de la empresa.
- **Por qué:** El churn es una clasificación binaria sobre datos tabulares, y el gradient boosting es el modelo
  sólido estándar para ello. El entrenamiento tarda segundos y no necesita GPU. El scoring tarda alrededor de un milisegundo por cliente.
  Ofrece probabilidades que se pueden evaluar frente a resultados reales, y explicaciones por cliente mediante SHAP.
- **Descartado:**
  - *LLM en Bedrock con prompts y reglas de negocio.* Un modelo fundacional no ha aprendido de nuestro historial
    de churn, no da una probabilidad medible ni calibrada, cuesta por token y tarda segundos por llamada,
    lo que es incompatible con los objetivos de latencia.
  - *El paquete `agentic` del scaffold.* No existe con esa API; era un marcador de posición.
- **Opción futura:** Una capa de LLM sobre los scores (para explicarlos o redactar acciones de retención)
  sigue siendo posible y no cambia nada de lo que hay aquí.

### D2. Predecir "cancela en los próximos 3 meses"

- **Decisión:** Horizonte de 3 meses, definido por `CHURN_HORIZON_MONTHS`.
- **Por qué:** Un mes deja a customer success muy poco tiempo para actuar. Seis meses mezcla causas que
  aún no se han reflejado en los datos. Tres meses también cubre bien la decisión de renovación de los contratos
  anuales.
- **Compromiso:** Un horizonte más largo implica que más snapshots recientes quedan sin etiqueta y no se pueden usar para
  entrenar. Con 3 meses, los snapshots de 2026-08 a 2026-10 todavía no tienen etiqueta, y el modelo aprende
  de los snapshots hasta 2026-07.

### D3. Granularidad mensual de los snapshots

- **Decisión:** Una fila por cliente y mes. Las features usan solo datos anteriores al primer día de
  ese mes.
- **Por qué:** La granularidad mensual encaja con cómo evoluciona el churn y cómo funciona la facturación, mantiene el dataset pequeño
  (clientes × meses) y hace que la fecha de referencia ("as of") no sea ambigua.
- **Descartado:** Snapshots diarios. Unas 30 veces más filas de datos casi idénticos y muy correlacionados,
  sin ningún beneficio para un horizonte de 3 meses.

### D4. División temporal en entrenamiento / validación / test

- **Decisión:** Los meses etiquetados más antiguos entrenan el modelo, los 3 meses siguientes eligen el número de
  árboles (early stopping) y los 3 meses más recientes se reportan como resultado de test. Después, el modelo final
  se reentrena con todos los meses etiquetados usando ese número de árboles.
- **Por qué:** En producción el modelo se entrena con el pasado y puntúa el futuro. Una división aleatoria
  pone meses contiguos del mismo cliente tanto en entrenamiento como en test, lo que infla las métricas.
- **Compromiso:** Los mismos clientes aparecen en entrenamiento y en test (en momentos distintos). Esto es realista,
  porque el modelo puntuará clientes que ya ha visto antes.

### D5. Sin reponderación de clases

- **Decisión:** `binary:logistic` simple, sin `scale_pos_weight`.
- **Por qué:** Alrededor del 15% de los clientes son positivos, lo que no es extremo. Sin reponderación, la salida
  sigue siendo una probabilidad utilizable (Brier score 0.070 en test), de la que dependen los umbrales absolutos de
  las bandas de riesgo (D6). La reponderación no mejora nada de lo que miden las métricas de ranking y rompe la calibración.

### D6. Umbrales de probabilidad fijos para las bandas de riesgo

- **Decisión:** `high` ≥ 0.30, `medium` ≥ 0.10, `low` en otro caso. Se guardan en los metadatos del modelo, de modo que los
  umbrales viajan con la versión del modelo.
- **Por qué:** Los umbrales absolutos significan lo mismo de un mes a otro ("al menos un 30% de probabilidad").
  Con bandas por percentil ("el 10% superior es alto"), un mes bueno seguiría marcando al 10% de los clientes.
- **Cómo se eligieron:** En los meses de test, ≥ 0.30 marca al 18% de los clientes y detecta al 71% de los
  que hacen churn, con un 49% de precisión. ≥ 0.10 detecta al 89% de los que hacen churn. Son puntos de partida: el
  umbral real depende de cuántas cuentas puede atender customer success, así que conviene revisarlo con negocio.

### D7. Explicar cada score con contribuciones SHAP

- **Decisión:** Guardar hasta 3 features con la mayor contribución SHAP positiva
  (`pred_contribs=True` en XGBoost) por cliente.
- **Por qué:** "Probabilidad 0.81" por sí sola no permite actuar. "El uso de licencias ha caído, 3 tickets de severidad alta" sí.
  El cálculo es nativo de XGBoost y no cuesta nada extra.
- **Advertencia:** Las contribuciones están en log-odds e incluyen interacciones. Por ejemplo, `seats` suele
  aparecer en cuentas grandes con poco uso: el modelo ha aprendido que el poco uso importa más cuando
  se pagan muchas licencias.

### D8. Un baseline de regresión logística en cada entrenamiento

- **Decisión:** Entrenar y evaluar una regresión logística estandarizada sobre la misma división, y guardar sus
  métricas en los metadatos del modelo.
- **Por qué:** Muestra si XGBoost justifica su complejidad (PR AUC 0.614 frente a 0.548 aquí). Si la diferencia llegara a
  desaparecer, el modelo más simple sería la mejor opción.

## Datos y almacenamiento

### D9. Estructura en S3: zona raw inmutable, zonas derivadas reconstruidas

- **Decisión:** `raw/` es solo de escritura incremental (append-only), particionada por origen y periodo. `features/` se reconstruye por completo
  a partir de `raw/` en cada ejecución. Los modelos están versionados. Ver [arquitectura.md](arquitectura.md#estructura-de-s3).
- **Por qué:** Los datos raw son la fuente de verdad y no se pueden recrear, mientras que los derivados sí, así que es
  seguro sobrescribirlos. Reconstruir las features desde cero hace que la corrección de un bug en el código de features se aplique a todo
  el histórico de una vez.
- **Corrige el bug del scaffold:** El `ingest_data` original siempre escribía `data/input.json`, así que cada
  subida destruía la anterior.

### D10. JSON Lines para los datos raw, Parquet para los derivados

- **Por qué:** Los sistemas de origen exportan JSON de forma natural, y JSON Lines permite añadir registros e inspeccionarlos
  a mano. Parquet es columnar, tipado y comprimido, y Athena lo lee de forma eficiente.

### D11. Claves de ingesta idempotentes

- **Decisión:** Los batches usan un `batch_id` indicado por quien llama (por defecto `part-0000`), de modo que volver a ejecutar una subida
  sobrescribe el mismo objeto en lugar de duplicar registros.
- **Nota para producción:** Una ingesta real en streaming usaría IDs de batch únicos (por ejemplo un UUID), con
  deduplicación por ID de registro (`ticket_id`, `event_id`) durante la construcción de features.

### D12. DynamoDB guarda solo el último score por cliente; el histórico va a S3

- **Decisión:** Clave de partición `CustomerID`, un item por cliente activo, sobrescrito en cada ejecución.
  Cada ejecución también se añade a `predictions/` en S3.
- **Por qué:** Las preguntas online son "¿Cuál es el riesgo del cliente X ahora?" y "¿Quién está en riesgo ahora?", que
  son una lectura clave-valor y una consulta a un índice. Las preguntas sobre el histórico (tendencias, si las predicciones pasadas se cumplieron)
  son analíticas, así que corresponden a S3 + Athena, que es más barato y más fácil de consultar.
- **Descartado:** Histórico en DynamoDB con clave de ordenación `scored_at`. Cada búsqueda necesitaría una
  consulta del "último item", y la tabla crecería sin ningún uso online.

### D13. Borrar los clientes que ya no están activos

- **Decisión:** Después de escribir, el scoring recorre la tabla con un scan y borra los items cuyo `snapshot_month` no es
  el actual.
- **Por qué:** Sin esto, un cliente que canceló conservaría su último score para siempre y seguiría apareciendo
  como "en riesgo".
- **Nota de escala:** Un scan está bien para miles o cientos de miles de items una vez al mes. A
  mayor escala, usa un atributo TTL o una comparación con las predicciones anteriores en S3.

### D14. GSI sobre `risk_band` + `churn_probability`

- **Por qué:** Lista los clientes con más riesgo en orden sin hacer un scan de la tabla.
- **Límite conocido:** `risk_band` tiene 3 valores, así que el índice tiene 3 claves de partición. Eso es suficiente hasta
  unos pocos miles de escrituras por segundo por banda, algo que un batch mensual nunca alcanza. Si alguna vez importa,
  divide la clave en shards (`high#0` … `high#9`) y combina los resultados.

### D15. Probabilidades guardadas como `Decimal`, redondeadas a 4 decimales

- **Por qué:** boto3 rechaza los `float` de Python para los números de DynamoDB. El `put_item` del scaffold habría
  fallado con la primera probabilidad. Cuatro decimales son más precisión de la que tiene el modelo.

### D16. Lecturas con consistencia eventual

- **Por qué:** Los scores cambian una vez por ejecución de scoring, así que una lectura que va una fracción de segundo por detrás nunca
  importa. Las lecturas con consistencia eventual cuestan la mitad y son el comportamiento por defecto de DynamoDB. La preocupación del README
  por las "lecturas consistentes" no aplica a una tabla escrita en batch.

## Servicio

### D17. Scoring en batch más una API de solo lectura, no inferencia en tiempo real

- **Decisión:** Los scores se calculan en batch. La API solo lee DynamoDB.
- **Por qué:** El riesgo de churn cambia a lo largo de semanas. El scoring en batch es más sencillo de operar y depurar, y el
  camino online se queda en un único `GetItem`, que es lo que hace realistas los objetivos de latencia (p99 < 150 ms a
  500 req/s).
- **Opción futura:** Volver a puntuar bajo demanda cuando ocurra un evento importante (downgrade, pago
  fallido) puede reutilizar `churn.scoring.score.score()` para un solo cliente.

### D18. FastAPI + uvicorn para la API

- **Por qué:** Es un servicio Python pequeño y estándar con validación de entrada (la banda debe ser
  `high|medium|low`, `limit` 1–500) y se puede probar en el mismo proceso. Se empaqueta en un contenedor para
  ECS o Lambda.

### D19. Pool de conexiones de boto3 de 50

- **Por qué:** El valor por defecto de 10 conexiones es menor que el pool de 40 hilos de FastAPI, así que bajo carga
  los hilos esperarían a que quedara libre una conexión.

## Entorno y herramientas

### D20. Un paquete, un subpaquete por servicio, código compartido en `churn.shared`

- **Decisión:** Todo el Python vive en `src/`: `src/pyproject.toml` define el paquete `churn`, con
  los tests en `src/tests/`. Dentro, `churn.training`, `churn.scoring` y `churn.api` son los tres
  servicios, `churn.shared` contiene lo que usa más de uno de ellos y `churn.localdev` contiene el
  job de seed exclusivo para local. Cada servicio tiene su propio punto de entrada (`python -m churn.<service>`). La
  raíz del repositorio no contiene archivos Python. Se eliminó la carpeta original del scaffold `src/src/`.
- **Por qué:** Los límites entre servicios son visibles en el código y coinciden con las unidades de despliegue. Un servicio
  puede importar de `shared`, nunca de otro servicio, de modo que cada uno se puede empaquetar por separado. Un solo
  paquete (no tres) mantiene una única versión, una suite de tests y un paso de instalación.
- **Descartado:**
  - Tres paquetes separados, cada uno con su propio `pyproject.toml`. Son más archivos y versiones que
    mantener sincronizados, para código que siempre se publica junto.
  - Un `requirements.txt` que contenga `-e .[dev]`. pip resuelve esa ruta respecto al directorio
    actual, no a la ubicación del archivo, así que solo funciona si se ejecuta desde dentro de `src/`.

### D21. LocalStack 4.4 para S3, con versión fijada

- **Por qué:** Un `latest` sin fijar cambia de comportamiento sin aviso, y las versiones recientes de LocalStack
  requieren un token de autenticación. 4.4 es la edición community y no necesita cuenta. Desde D24 solo
  emula S3.

### D22. Los tests se ejecutan con moto, no con los emuladores

- **Por qué:** moto simula AWS en el mismo proceso: sin Docker, unos 30 segundos para la suite completa, y funciona en CI
  tal cual. Los emuladores se usan para el entorno interactivo y la prueba de carga.

### D23. Configuración solo mediante variables de entorno; los endpoints se dejan a boto3

- **Decisión:** El código no tiene ningún ajuste de endpoint. boto3 lee por sí mismo `AWS_ENDPOINT_URL` (todos los servicios) o
  `AWS_ENDPOINT_URL_S3` / `AWS_ENDPOINT_URL_DYNAMODB` (un servicio). En AWS no se define ninguna.
- **Por qué:** El mismo código se ejecuta en los emuladores, en moto y en AWS, y nada en él sabe en qué
  entorno está. Las variables por servicio son lo que permite que S3 y DynamoDB vivan en contenedores
  distintos (D24). Ver [ejecucion-local.md](ejecucion-local.md#variables-de-entorno).
- **Detalle:** Cuando se define un endpoint de S3, el cliente usa direccionamiento de bucket de tipo path. El estilo
  virtual-host por defecto buscaría `<bucket>.localstack` en el DNS, que no existe.

### D24. dynamodb-local para DynamoDB en lugar de LocalStack

- **Decisión:** DynamoDB se ejecuta en el contenedor `amazon/dynamodb-local` (en memoria, `-sharedDb`);
  LocalStack se queda solo con S3.
- **Por qué:** Medido sobre los mismos 2,000 items, dynamodb-local sirvió 268 `GetItem`/s con p99 238 ms con
  32 clientes, frente a 90/s con p99 914 ms de LocalStack. En la prueba de k6 duplicó la tasa servida por la API
  (196 frente a ~90 req/s) sin errores. Ver
  [rendimiento.md](rendimiento.md#throughput-del-emulador).
- **Descartado:**
  - MinIO para S3. La prueba de carga nunca toca S3, y el S3 de LocalStack ya se comporta como AWS.
  - Mantenerlo todo en LocalStack. Un contenedor menos, pero la mitad de throughput y una latencia de cola
    mucho peor.
- **Límite:** Incluso dynamodb-local se queda en torno a 270 req/s. Los objetivos de latencia solo se pueden comprobar
  contra DynamoDB real ([produccion.md](produccion.md#probar-los-objetivos-de-latencia-contra-dynamodb-real)).

### D25. Los jobs batch se ejecutan al arrancar y terminan; no se quedan levantados sin hacer nada

- **Decisión:** En Compose, `seed`, `training` y `scoring` se ejecutan una vez con `docker compose up`, en ese
  orden (`depends_on: service_completed_successfully`), y después terminan. La API arranca solo cuando el scoring
  ha terminado con éxito. Un job se vuelve a ejecutar con `docker compose run --rm --no-deps <job>`.
- **Por qué:** Refleja AWS, donde una tarea programada o una Lambda arranca, trabaja y se detiene. El código de salida
  muestra si un job ha funcionado, y Compose no arranca el siguiente si ha fallado. Los contenedores inactivos
  ocultarían los fallos tras un estado "Up" y necesitarían un bucle keep-alive ficticio que no existe en
  producción.
- **Descartado:** Jobs que se quedan levantados y se ejecutan con un temporizador dentro del contenedor (cron). Eso añade un
  planificador a la imagen que AWS (EventBridge) proporciona fuera de ella.

### D26. Dos imágenes a partir de un Dockerfile, dependencias separadas por servicio

- **Decisión:** El target `api` instala `churn[api]` (boto3, FastAPI, uvicorn: 256 MB). El target `jobs`
  instala `churn[jobs]` (+ pandas, XGBoost, scikit-learn: 1.74 GB) y ejecuta seed, training
  o scoring según su comando.
- **Por qué:** La API es el servicio sensible a la latencia y que posiblemente vaya en Lambda. Dejar pandas y XGBoost fuera
  de ella hace que su imagen sea pequeña y sus arranques en frío rápidos. Training y scoring necesitan las mismas
  librerías, así que una imagen separada para cada uno solo las duplicaría.
- **Se garantiza mediante:** `churn.shared.aws` y `churn.shared.config` solo importan boto3; los helpers de pandas viven
  en `churn.shared.s3_io`, que la API nunca importa. La CLI `churn` importa el servicio de cada comando
  de forma diferida por el mismo motivo.

### D27. Training y scoring reconstruyen las features cada uno antes de trabajar

- **Por qué:** Cada job es autónomo: el scoring nunca depende de que el training se acabe de ejecutar, y
  cualquiera de los dos se puede volver a ejecutar por separado. La reconstrucción es determinista y tarda segundos con este tamaño.
- **A escala:** Convierte la construcción de features en un cuarto job (ejecutado una vez al mes antes de los otros dos) cuando
  pase a ser costosa.

## Monitorización, observabilidad y frontend

### D28. La monitorización es un cuarto servicio que evalúa las predicciones frente a los resultados reales

- **Decisión:** `churn.monitoring` se ejecuta cada mes después del scoring. Para cada mes de `predictions/` cuya
  ventana de etiqueta de 3 meses se ha cerrado, cruza las predicciones con el resultado real y calcula las
  mismas métricas que el training reporta en sus meses de test (código compartido: `churn.shared.metrics`). Los meses
  que siguen dentro de su ventana se listan como pendientes. Salida: `monitoring/model_performance.json`.
- **Por qué:** Las métricas de test del training describen el pasado. Esta es la única medición de cómo rinde realmente el
  modelo desplegado, y sustituye el "95% de precisión" no verificable del README por cifras que se
  siguen mes a mes.
- **Origen del resultado:** la etiqueta del snapshot de features del cliente para ese mes, reconstruida a partir de los
  eventos de facturación. Es la misma definición de churn con la que se entrenó el modelo, así que la comparación es
  homogénea.

### D29. Backtest point-in-time para crear un histórico de predicciones en local

- **Decisión:** Una herramienta local (`churn.localdev.backtest`) reproduce el pasado. Para cada mes M entrena
  un modelo solo con snapshots cuyo resultado se conocía antes de M (snapshot ≤ M − horizonte), puntúa
  el mes M y lo guarda como `model_version = backtest-M`. Con los datos por defecto eso va de 2026-01 a
  2026-07: los meses anteriores no tienen 7 meses de histórico con los que entrenar, y los posteriores aún no tienen resultado.
- **Por qué:** Los datos sintéticos no tienen histórico de predicciones pasadas, y esperar meses a los resultados no
  es una opción. Puntuar meses pasados con el modelo actual sería leakage: se entrenó precisamente con esos
  meses, así que las métricas parecerían mejores que la realidad. `test_backtest_only_learns_from_outcomes_known_before_the_month`
  comprueba el corte.
- **Excepción a D20:** como herramienta local, no un servicio, importa directamente el código de training y scoring.
  Los servicios siguen sin importarse nunca entre sí.

### D30. Prometheus + Grafana para las métricas operativas; k6 envía mediante remote write

- **Decisión:**
  - La API expone `/metrics` con un histograma de duración de peticiones (método, plantilla de ruta, estado) y
    un histograma de llamadas a DynamoDB (operación).
  - Prometheus lo recoge mediante scraping; k6 envía sus propias métricas al mismo Prometheus con remote write.
  - Un dashboard de Grafana aprovisionado muestra ambas, con los objetivos dibujados como líneas. Grafana se ejecuta con
    acceso de administrador anónimo, solo para uso local.
- **Por qué:** El README pide los resultados de la prueba de carga en Grafana. La latencia del lado del servidor y del lado del cliente,
  una junto a otra, muestran dónde se pierde el tiempo: el panel de DynamoDB junto al panel de la API hace visible el cuello de botella
  del emulador en lugar de tener que argumentarlo.
- **Detalles:**
  - Las peticiones se etiquetan por plantilla de ruta (`/customers/{customer_id}/churn`), nunca por la ruta en bruto, lo que
    crearía una serie temporal por cliente.
  - Los 4 procesos worker de uvicorn comparten métricas a través de `PROMETHEUS_MULTIPROC_DIR`, que se vacía al arrancar.
- **Descartado:** InfluxDB como backend de k6. Una segunda base de datos de series temporales, cuando Prometheus ya es
  necesario para las métricas de la API.
- **En AWS:** Amazon Managed Prometheus + Managed Grafana, o métricas de CloudWatch (ver produccion.md).

### D31. Calidad del modelo en el frontend, operaciones en Grafana

- **Por qué:** La calidad del modelo es una pregunta de negocio (¿cuántos clientes marcados hacen churn realmente?) para las
  personas que usan el frontend. La latencia y la saturación son preguntas de ingeniería. Enviar métricas mensuales del
  modelo a Prometheus requeriría un Pushgateway para cifras que cambian una vez al mes.

### D32. Frontend estático (HTML + JS) detrás de nginx, con `/api` redirigido a la API

- **Decisión:** `frontend/public/` contiene tres archivos sin paso de build y sin librerías externas. El
  gráfico es SVG inline. nginx los sirve y reenvía `/api/*` al contenedor de la API.
- **Por qué:**
  - Un único origen para el navegador, así que no hace falta configurar CORS.
  - La misma forma que el alojamiento en AWS: S3 + CloudFront, con un behavior `/api` que apunta a API
    Gateway o al balanceador de carga.
  - Ninguna toolchain de Node que mantener.
  - Ninguna dependencia de CDN, así que la página funciona sin conexión.
- **Descartado:**
  - React + Vite. Más realista para un producto, pero añade Node y un paso de build para tres vistas.
  - Streamlit. Un cuarto servicio Python que no es un frontend de verdad.
- **Accesibilidad y seguridad:**
  - Los valores de la API se insertan con `textContent`, nunca con `innerHTML`.
  - Las bandas de riesgo siempre muestran un icono y una etiqueta de texto, nunca solo el color.
  - El gráfico tiene leyenda, una cruceta navegable con el teclado y una vista de tabla.
  - Los temas claro y oscuro usan paletas validadas.

### D33. Recuentos por banda con consultas `Select=COUNT`

- **Decisión:** `GET /churn/summary` ejecuta una consulta `COUNT` por banda sobre `RiskBandIndex`.
- **Por qué:** Recuentos exactos sin transferir items, y sin tabla ni contador adicional que mantener sincronizado.
  Tres consultas por carga de página es insignificante a esta escala. Con millones de clientes, haz que el scoring
  escriba los recuentos una vez por ejecución.
