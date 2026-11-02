# Estado de la especificación

Cada punto de la especificación original (el `README.md` de la raíz) comparado con lo que está implementado.
Actualizado el 2026-10-31.

| Punto de la especificación | Estado | Notas |
|---|---|---|
| **Problema / solución:** predicción de churn con Python, S3, DynamoDB | ✅ Hecho | Cuatro servicios más un frontend, todos ejecutándose en Docker Compose |
| **"agentic"** como herramienta de ML | ⚠️ Sustituido a propósito | No existe como librería. XGBoost hace la predicción ([decisión D1](decisiones.md#d1-predecir-el-churn-con-xgboost-entrenado-con-nuestros-propios-datos-no-con-un-llm-ni-un-framework-agéntico)) |
| **Impacto:** "95% de precisión" | ❌ No alcanzado, ahora medido | Se sigue cada mes con resultados reales: precisión de la banda alta 49–61%, recall ~73%, ROC AUC 0.91–0.94. La precisión varía con la tasa de churn, así que un único "95%" no sirve para juzgar el modelo ([modelo.md](modelo.md#resultados-de-la-monitorización)) |
| **Impacto:** "10,000 clientes menos perdidos al año" | ❌ No se puede medir | Necesita datos reales y un efecto de retención medido |
| **Fase 1:** definir el modelo de churn | ✅ Hecho | Horizonte de 3 meses, snapshots mensuales, 14 features, tests de leakage |
| **Fase 2:** prototipo en Python | ✅ Hecho | Training, scoring, monitoring, API, frontend |
| **Fase 3:** fuentes de datos en tiempo real y ajuste del modelo | ❌ No iniciado | Sin datos reales; sin búsqueda de hiperparámetros |
| **Fase 4:** despliegue en AWS con monitorización de precisión y retención | ⚠️ Parcial | Hecho en local: servicio de monitorización de la precisión, dashboards de Grafana. Falta: cómputo en AWS, IAM, programaciones, alertas, comprobaciones de drift, métricas de retención |
| **Arquitectura:** app SaaS → S3 | ⚠️ Simulado | El job de seed sustituye a los sistemas de origen |
| **Arquitectura:** modelo, DynamoDB | ✅ Hecho | — |
| **Arquitectura:** Frontend App | ✅ Hecho | HTML + JS estático detrás de nginx: recuentos por banda, lista de clientes en riesgo, detalle de cliente, rendimiento del modelo. Modo claro/oscuro, ancho de móvil |
| **Entorno local** con S3 y DynamoDB emulados | ✅ Hecho | LocalStack para S3, dynamodb-local para DynamoDB, más Prometheus y Grafana |
| **Deploy con `terraform apply`** | ⚠️ Parcial | Solo la capa de datos (bucket + tabla), nunca aplicado |
| **Prueba de carga con k6** | ✅ Hecho | Escenario de tasa constante (pasa/falla según los objetivos) y escenario de rampa (punto de saturación) |
| **Resultados de la prueba de carga en Grafana** | ✅ Hecho | Un dashboard con métricas del lado de la API y de k6, con los objetivos dibujados como líneas |
| **500 req/s, p99 < 150 ms, < 0.5% de errores** | ❌ No alcanzado en local | 0% de errores en todas las ejecuciones. El p99 cumple el objetivo hasta ~100 req/s constantes. La propia API se satura pronto ([rendimiento.md](rendimiento.md)); correcciones y después una prueba en AWS real |
| **Punto de saturación en torno a 1,500 req/s** | ⚠️ Medido solo en local | La latencia empeora a partir de ~100 req/s y el throughput llega como máximo a ~190–235 req/s. Valor en producción desconocido |
| **Tests unitarios y de integración** | ✅ Hecho | 5 tests: leakage, etiquetas, corte del backtest, monitorización, ejecución completa de todos los servicios |
| **Reto:** ML en tiempo real | ⚠️ Replanteado a propósito | El scoring se ejecuta en batch; solo las lecturas son en tiempo real ([D17](decisiones.md#d17-scoring-en-batch-más-una-api-de-solo-lectura-no-inferencia-en-tiempo-real)) |
| **Reto:** consistencia eventual de DynamoDB | ✅ Resuelto por diseño | Un único escritor (scoring), las lecturas se hacen después ([D16](decisiones.md#d16-lecturas-con-consistencia-eventual)) |
| **Ruta de aprendizaje:** "agentic" | ⚠️ No aplicable tal como está escrito | Una capa de LLM sobre los scores sigue siendo opcional ([produccion.md](produccion.md#trabajo-opcional-futuro)) |

## Trabajo pendiente

1. **Correcciones de rendimiento de la API:** caché, componentes de servidor más rápidos, middleware más ligero, menos hilos.
   Ver [rendimiento.md](rendimiento.md#correcciones-propuestas).
2. **Despliegue en AWS y prueba de carga en la misma región**, la única forma de verificar 500 req/s con
   p99 < 150 ms ([produccion.md](produccion.md#probar-los-objetivos-de-latencia-contra-dynamodb-real)).
   Necesita una cuenta de AWS.
3. **Fase 3:** fuentes de datos reales ([produccion.md](produccion.md#datos-reales)) y ajuste del modelo.
4. **Control de versiones:** todavía no hay nada commiteado en git.
