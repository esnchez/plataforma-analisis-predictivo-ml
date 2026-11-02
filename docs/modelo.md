# Modelo

Código: `src/churn/training/` (servicio de entrenamiento) y `src/churn/scoring/` (servicio de scoring).

## Procedimiento de entrenamiento

1. Cargar todos los snapshots etiquetados de `features/`.
2. Dividir por mes del snapshot (decisión D4). Con los datos por defecto:

   | División | Meses | Función |
   |---|---|---|
   | Entrenamiento | 2025-04 → 2026-01 (10 meses) | Ajustar los árboles |
   | Validación | 2026-02 → 2026-04 (3 meses) | Early stopping: elegir el número de árboles |
   | Test | 2026-05 → 2026-07 (3 meses) | Métricas publicadas; nunca se usa para ninguna elección |

3. Entrenar XGBoost con hasta 1,000 árboles, parando cuando el PR AUC de validación no ha mejorado en 50
   rondas. Esta ejecución paró en 210 árboles.
4. Evaluar en test. Entrenar la regresión logística de referencia con la misma división.
5. Reajustar con todos los meses etiquetados (13,440 filas) y 210 árboles. Este es el modelo publicado: los
   meses más recientes son los más relevantes para el futuro, así que no deben quedar fuera.
6. Publicar `model.json` y `metadata.json` en `models/churn/<version>/` y después actualizar `latest.json`.
   El puntero se escribe al final para que el scoring nunca recoja una versión a medio escribir.

Hiperparámetros (`XGB_PARAMS`): `max_depth=4`, `learning_rate=0.05`, `min_child_weight=5`,
`subsample=0.8`, `colsample_bytree=0.8`. Son valores por defecto conservadores para un dataset de este tamaño,
no el resultado de una búsqueda de ajuste. Ajústalos solo si los datos reales lo hacen necesario, y siempre con los
meses de validación, nunca con test.

## Resultados (ejecución sintética por defecto)

Meses de test 2026-05 → 2026-07: 2,612 snapshots de cliente, 320 con churn (tasa base del 12.25%).

| Métrica | XGBoost | Regresión logística | Qué significa |
|---|---|---|---|
| ROC AUC | **0.916** | 0.888 | Probabilidad de que un cliente con churn elegido al azar obtenga una puntuación más alta que uno sin churn elegido al azar |
| PR AUC | **0.614** | 0.548 | Precisión media en todos los umbrales; el resumen honesto cuando los positivos son escasos (aleatorio = 0.12) |
| Brier score | 0.070 | — | Error cuadrático medio de las probabilidades; cuanto más bajo, mejor calibrado |
| Precisión en el decil superior | 63.2% | — | Del 10% de clientes con puntuación más alta, cuántos hicieron churn |
| Lift en el decil superior | 5.2× | — | ...comparado con la tasa base del 12.25% |

Bandas de riesgo en los meses de test (marcado = en el umbral o por encima, así que `medium` incluye `high`):

| Umbral | Clientes marcados | Precisión | Recall |
|---|---|---|---|
| ≥ 0.30 (`high`) | 462 (17.7%) | 49.4% | 71.3% |
| ≥ 0.10 (`medium` o `high`) | 847 (32.4%) | 33.8% | 89.4% |

Cómo leer la fila `high`: si customer success contacta con las 462 cuentas de riesgo alto, aproximadamente la mitad
habría hecho churn de verdad, y esos contactos llegan al 71% de todos los futuros clientes con churn.

El scoring del snapshot actual (2026-10) dio 164 high, 155 medium y 570 low de 889 clientes
activos.

### Sobre la "precisión del 95%" del README

El README original afirmaba una precisión del 95%. Esa cifra era un objetivo, no una medición, y por sí sola
no es útil:

- La **accuracy** puede ser del 87.75% aquí prediciendo "nadie hace churn", así que una accuracy alta no demuestra nada.
- La **precisión** depende del umbral que elijas. Subir el umbral aumenta la precisión y
  detecta menos clientes con churn.
- Publica precisión **y** recall en el umbral que usa el negocio, además del PR AUC, como arriba.

Con datos reales, espera cifras más bajas que en esta ejecución sintética (ver
[datos-sinteticos.md](datos-sinteticos.md#qué-no-es-realista-y-por-qué-importa)).

### Features más importantes (gain)

`months_to_renewal` (161) va muy por delante de `active_seat_ratio_3m_avg` (42), `active_seat_ratio_1m` (29),
`feature_events_3m` (21), `is_annual` (21) y `high_sev_tickets_3m` (17). Esto coincide con el simulador:
los contratos anuales solo pueden hacer churn en la renovación, y la salud se refleja de forma más directa en el uso de licencias.
`logins_trend` queda abajo porque la salud del simulador cambia gradualmente, así que el nivel actual
ya contiene la mayor parte de la información.

## Metadatos del modelo

`models/churn/<version>/metadata.json` contiene todo lo necesario para reproducir y auditar una versión:

- Lista de features, en orden. El scoring la usa, así que el código de features y el modelo no pueden discrepar sin que se note.
- Umbrales de las bandas de riesgo y horizonte.
- Hiperparámetros, incluido el número final de árboles, y la versión de XGBoost.
- Rangos de meses de entrenamiento, validación y test, y el número de filas del ajuste final.
- Métricas de test, métricas de la referencia e importancia de las features.

## Scoring

El job de scoring carga `latest.json` → modelo + metadatos y puntúa el último snapshot (o
`--snapshot-month`). Para cada cliente calcula:

- `churn_probability`
- `risk_band` a partir de los umbrales de los metadatos
- `top_factors`: hasta 3 features con la mayor contribución SHAP positiva (log-odds), con sus
  valores

Escribe en DynamoDB, borra los elementos obsoletos y añade la ejecución a `predictions/` (decisiones D7, D12, D13).

## Monitorización: resultados reales frente a predicciones

Código: `src/churn/monitoring/` (el servicio de monitorización) y `src/churn/localdev/backtest.py`
(decisiones D28, D29).

Cada mes, después del scoring, el job de monitorización revisa cada mes en `predictions/`:

- **Resultado conocido** (la ventana de 3 meses posterior a ese mes ya ha pasado por completo): une las predicciones
  con lo que ocurrió y calcula las mismas métricas que el conjunto de test del entrenamiento. Entre ellas, la
  precisión y el recall de la banda high, las cifras que percibe customer success.
- **Resultado aún abierto:** el mes aparece como pendiente.

El informe va a `monitoring/model_performance.json`; la API lo sirve y el frontend lo muestra en gráficos.

Los datos sintéticos no tienen predicciones pasadas, así que en local un **backtest** las crea. Para cada mes
2026-01 → 2026-07 entrena un modelo solo con snapshots cuyo resultado ya se conocía antes de ese
mes, y después puntúa ese mes. Por ejemplo, el modelo de enero aprendió de snapshots hasta octubre de
2025, porque la ventana de 3 meses de noviembre termina en enero. Es exactamente lo que habría predicho un modelo desplegado
en ese momento.

### Resultados de la monitorización

Ejecución sintética por defecto, un modelo point-in-time por mes:

| Mes | Clientes | Con churn | ROC AUC | PR AUC | Banda high: marcados | Precisión | Recall | Medium o high: recall |
|---|---|---|---|---|---|---|---|---|
| 2026-01 | 840 | 14.9% | 0.928 | 0.710 | 136 | 60.3% | 65.6% | 96.0% |
| 2026-02 | 833 | 14.6% | 0.940 | 0.740 | 155 | 58.7% | 74.6% | 95.1% |
| 2026-03 | 841 | 14.4% | 0.941 | 0.734 | 150 | 60.7% | 75.2% | 92.6% |
| 2026-04 | 851 | 13.2% | 0.926 | 0.690 | 149 | 55.0% | 73.2% | 92.9% |
| 2026-05 | 875 | 11.8% | 0.913 | 0.622 | 153 | 48.4% | 71.8% | 88.3% |
| 2026-06 | 865 | 12.2% | 0.922 | 0.640 | 160 | 48.7% | 73.6% | 90.6% |
| 2026-07 | 872 | 12.7% | 0.921 | 0.646 | 164 | 49.4% | 73.0% | 93.7% |

Cómo leerlo:

- **La ordenación del modelo es estable:** ROC AUC de 0.91–0.94 todos los meses. La puntuación de test del propio job
  de entrenamiento (0.916) coincide con lo que se vio después con los resultados reales.
- **El recall también es estable:** la banda high detecta alrededor del 73% de los clientes que cancelan más tarde, y las
  bandas medium o high alrededor del 90%.
- **La precisión bajó de ~60% a ~49% en mayo, sin que el modelo empeorara.** En mayo la propia tasa de churn
  bajó de alrededor del 14–15% a alrededor del 12%. Con menos clientes con churn, el mismo umbral marca aproximadamente
  el mismo número de clientes, pero menos de ellos hacen churn. Por eso la precisión por sí sola (como el
  "95%" del README) no sirve para juzgar un modelo de churn: se mueve con la tasa base. Lee la precisión junto a
  la tasa base, y usa ROC/PR AUC para la calidad del modelo.
- **Qué sería preocupante:** una caída del ROC AUC, o una caída del recall con una tasa de churn estable. Cualquiera de las dos
  significa que el modelo está perdiendo el contacto con la realidad y necesita reentrenamiento o nuevas features.

Octubre de 2026 (las puntuaciones actuales) sigue pendiente hasta que su ventana se cierre en diciembre de 2026.

## Reentrenamiento y drift (pendiente con datos reales)

- **Reentrenar** mensual o trimestralmente. Promocionar una versión nueva solo si su PR AUC de test no es peor que
  el de la actual en los mismos meses de test.
- **Alertar sobre el informe de monitorización:** por ejemplo, ROC AUC por debajo de 0.85, o un recall de la banda high que cae
  más de 10 puntos mientras la tasa de churn es estable.
- **Vigilar el drift:** comparar las distribuciones de las features y la proporción de cada banda de riesgo con los meses
  anteriores. Un salto repentino en `high` suele indicar un problema de datos antes que una ola de churn.
