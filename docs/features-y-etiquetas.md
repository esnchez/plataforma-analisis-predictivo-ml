# Features y etiquetas

Código: `src/churn/shared/features.py` (lo usan tanto el servicio de training como el de scoring). Tests: `src/tests/test_features.py`.

## El snapshot

Un **snapshot del mes T** describe a un cliente tal como estaba el primer día del mes T.

```
      ... T-6  T-5  T-4 │ T-3  T-2  T-1 ║  T   T+1  T+2 │ T+3 ...
      └ 3 meses antes ┘   └ últ. 3 m. ┘ ║  └ ventana etiq. ┘
                las features usan estos ║  etiqueta: ¿cancela aquí el cliente?
                                        ║
                           fecha del snapshot (día 1 de T)
```

- Las **features** usan solo meses anteriores a T.
- **Etiqueta** = 1 si el cliente cancela en los meses T, T+1 o T+2 (horizonte 3); si no, 0.
- Un cliente aparece en el snapshot T solo si se dio de alta antes de T y no había cancelado antes de T.

La misma función construye las filas de training y de scoring. Las filas de training son snapshots cuya ventana de
etiqueta está entera en el pasado. La fila de scoring es el snapshot del mes siguiente al último dato
(2026-10 para datos que terminan en 2026-09), cuya etiqueta todavía es desconocida.

## Features

Todas las features de actividad se calculan sobre una cuadrícula cliente × mes que cubre los meses en los que el cliente
estuvo suscrito. Un mes sin actividad cuenta como 0, no como dato ausente, así que las ventanas de "3 meses" siempre significan
3 meses naturales.

| Feature | Definición | Por qué debería importar |
|---|---|---|
| `tenure_months` | Meses desde el alta hasta T | Los clientes nuevos hacen más churn; los clientes antiguos son fieles |
| `plan_tier` | basic 0, pro 1, enterprise 2 | Los planes más grandes hacen menos churn |
| `is_annual` | 1 si el contrato es anual | Los clientes anuales solo pueden irse en la renovación |
| `seats` | Licencias pagadas | Tamaño de la cuenta; cambia cómo debe interpretarse el uso |
| `months_to_renewal` | Anual: meses hasta el siguiente aniversario (1–12). Mensual: siempre 1 | El churn anual se concentra justo antes de la renovación |
| `logins_1m` | Logins en T-1 | Engagement actual |
| `logins_3m_avg` | Media mensual de logins en T-3..T-1 | Engagement suavizado |
| `logins_trend` | (media T-3..T-1 + 1) / (media T-6..T-4 + 1); 1.0 sin histórico | Un engagement en descenso es una señal clásica de churn |
| `active_seat_ratio_1m` | Licencias activas / licencias pagadas en T-1 | Pagar por licencias que nadie usa es un motivo para irse |
| `active_seat_ratio_3m_avg` | Media de lo anterior durante 3 meses | Suavizado |
| `feature_events_3m` | Eventos de uso de funcionalidades en T-3..T-1 | Profundidad de uso, no solo iniciar sesión |
| `tickets_3m` | Tickets de soporte en T-3..T-1 | Fricción |
| `high_sev_tickets_3m` | Tickets de severidad alta en T-3..T-1 | Fricción grave |
| `payment_failures_3m` | Pagos fallidos en T-3..T-1 | Churn involuntario y problemas de presupuesto |

`industry` y `country` están en los datos del CRM pero no se usan. No tienen ningún efecto en el simulador y,
con datos reales, solo deberían añadirse si mejoran las métricas de test. Además necesitarían una
revisión de equidad (fairness) antes de usarse.

## Protecciones contra leakage

Leakage significa que una feature contiene información de la ventana de etiqueta. Hace que las métricas offline
parezcan excelentes y que el modelo en producción sea inútil.

1. **Corte temporal estricto.** La fila de la cuadrícula del mes m se convierte en el snapshot m+1, así que un snapshot nunca puede ver
   su propio mes. `test_features_ignore_data_from_the_snapshot_month_onwards` asigna al uso de junio de 2026
   valores absurdos y comprueba que el snapshot de junio de 2026 no cambia.
2. **Los clientes que cancelan salen de la población.** Un cliente que canceló en el mes m no está en el
   snapshot m+1 ni en los posteriores, así que el modelo nunca ve "un cliente con actividad cero porque
   ya se había ido" etiquetado como churn.
3. **Las etiquetas desconocidas siguen siendo desconocidas.** Si T+2 es posterior al último mes de datos, la etiqueta es `NaN`, incluso para
   clientes que ya cancelaron dentro de la parte observada de la ventana. Etiquetar solo a esos
   haría que los meses recientes parecieran tener más churn del que tuvieron.
4. **Warm-up.** Se descartan los snapshots de los primeros 6 meses de la ventana de datos, porque sus
   ventanas móviles (hasta 6 meses para `logins_trend`) quedarían recortadas por la ventana, no por la
   antigüedad del cliente.
5. **Aquí los campos del CRM son estáticos.** Con datos reales, el plan y las licencias deben tomarse a fecha de T (ver
   [datos-sinteticos.md](datos-sinteticos.md#qué-no-es-realista-y-por-qué-importa)).

`test_labels_follow_the_cancellation_window` comprueba las protecciones 2–4 con un ejemplo de 2 clientes construido a mano.

## Almacenamiento

Un fichero Parquet por mes de snapshot: `features/snapshot_month=YYYY-MM/features.parquet`, con las columnas
`customer_id`, `snapshot_month`, las 14 features y `label` (float: 0, 1 o NaN). Todo el prefijo se
reconstruye a partir de `raw/` al principio de cada ejecución de training y de scoring.
