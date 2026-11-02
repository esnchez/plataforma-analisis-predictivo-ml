# Datos sintéticos

Todavía no hay datos reales de clientes. `src/churn/synthetic.py` simula una base de clientes SaaS y
devuelve su actividad como las cuatro fuentes en bruto que tendría una empresa real. El pipeline posterior a
`raw/` no sabe que los datos son sintéticos. Sustituir el simulador por exportaciones reales con los mismos
esquemas es el único cambio necesario (ver [produccion.md](produccion.md)).

## Cómo se comportan los clientes simulados

Valores por defecto: 2,000 clientes, 24 meses hasta septiembre de 2026, semilla 42 (`python -m churn.localdev --help`).

1. **Perfil del cliente:**
   - Plan: basic 50%, pro 35%, enterprise 15%.
   - Licencias: 1–5 en basic, 5–30 en pro, 30–300 en enterprise.
   - Contrato: anual con una probabilidad del 20% / 50% / 90% según el plan; si no, mensual.
   - El sector y el país son aleatorios y **no influyen** en el comportamiento.
2. **Alta:** el 40% de los clientes ya existe cuando empieza la ventana (se dieron de alta entre 1 y 36 meses antes);
   el resto se da de alta en un mes aleatorio dentro de la ventana.
3. **Salud oculta:** cada cliente empieza con una puntuación de salud que depende del plan (enterprise
   más sano). Cada mes da un pequeño paso aleatorio con una ligera deriva a la baja. Con una probabilidad del 3%
   al mes cae bruscamente, simulando algo como perder al principal defensor interno del producto.
   **El modelo nunca ve la salud.**
4. **Qué determina la salud cada mes:**
   - Licencias activas: una parte de las licencias pagadas, menor cuando la salud es baja.
   - Logins: unos 12 por licencia activa, menos cuando la salud es baja.
   - Eventos de features: proporcionales a los logins y a la salud.
   - Tickets de soporte: más cuando la salud es baja y en cuentas más grandes. Un ticket de severidad alta es
     más probable cuando la salud es baja.
   - Facturas: los contratos mensuales se facturan cada mes, los anuales en cada aniversario. Un
     pago falla con más frecuencia cuando la salud es baja; el 70% de los pagos fallidos se recuperan el mismo mes.
5. **Cancelación:**
   - Los contratos mensuales pueden cancelarse en cualquier mes después del primero. Los contratos anuales solo pueden cancelarse en
     el último mes de un año de contrato, así que su riesgo se concentra ahí.
   - La probabilidad aumenta con una salud baja, un pago fallido en los últimos 2 meses, tickets de severidad alta
     ese mes y estar en los primeros 6 meses.
   - Una cancelación es un evento de facturación `subscription_cancelled`. El uso de ese mes se sigue
     registrando, y nada después.

## Datos resultantes (ejecución por defecto)

| | |
|---|---|
| Registros en bruto | 55,592 en 73 objetos de S3 |
| Clientes aún activos al final | 889 de 2,000 |
| Filas de snapshot / filas etiquetadas | 16,109 / 13,440 |
| Tasa de churn a 3 meses (snapshots etiquetados) | 14.6% en total, 12–18% según el mes |
| Por contrato | mensual 20.8%, anual 9.5% |
| Por plan | basic 19.5%, pro 12.1%, enterprise 9.4% |

Un churn mensual de alrededor del 5% es alto pero plausible para una base dominada por cuentas pequeñas. Los productos
B2B enterprise suelen tener menos churn. Las constantes del simulador están al principio de `synthetic.py`.

## Esquemas de los registros en bruto

Estos son los contratos para las integraciones reales.

**`crm_customers`** — exportación completa de la tabla, partición `export_date=YYYY-MM-DD`

| Campo | Ejemplo |
|---|---|
| `customer_id` | `C-00042` |
| `signup_date` | `2025-03-14` |
| `plan` | `basic` / `pro` / `enterprise` |
| `seats` | `12` |
| `contract` | `monthly` / `annual` |
| `industry`, `country` | `retail`, `ES` |

**`usage`** — un registro por cliente y mes activo, partición `month=YYYY-MM`

| Campo | Ejemplo |
|---|---|
| `customer_id`, `month` | `C-00042`, `2026-09` |
| `active_seats` | `9` |
| `logins` | `140` |
| `feature_events` | `61` |

**`support_tickets`** — un registro por ticket, partición `month=YYYY-MM`

| Campo | Ejemplo |
|---|---|
| `ticket_id`, `customer_id` | `T-0001234`, `C-00042` |
| `created_at` | `2026-09-03T14:22:10Z` (UTC) |
| `severity` | `low` / `medium` / `high` |
| `channel` | `email` / `chat` / `phone` |

**`billing_events`** — un registro por evento, partición `month=YYYY-MM`

| Campo | Ejemplo |
|---|---|
| `event_id`, `customer_id` | `B-0004321`, `C-00042` |
| `event_type` | `invoice_paid` / `payment_failed` / `subscription_cancelled` |
| `occurred_at` | `2026-09-01T08:00:00Z` (UTC) |
| `amount_usd` | `480` (0 en las cancelaciones) |

## Qué no es realista (y por qué importa)

- **Perfil de CRM estático.** El plan y las licencias nunca cambian, así que las features pueden leerlos de la última
  exportación. Con datos reales hay subidas y bajadas de plan, y la última exportación muestra los valores
  de hoy, que se filtrarían a snapshots antiguos. Los datos reales necesitan histórico del CRM (una exportación diaria por
  partición, o eventos de cambio), unido por la fecha del snapshot.
- **Agregados mensuales de uso en lugar de eventos en bruto.** La analítica de producto real emite eventos
  (login, feature usada), que se agregarían en estos campos mensuales durante la construcción de features.
- **Una única causa limpia de churn.** El churn real también viene de recortes de presupuesto, adquisiciones y competidores,
  que no dejan rastro en los datos de producto. Espera métricas más bajas con datos reales.
- **No faltan datos ni llegan tarde.** Las fuentes reales pierden días, llegan tarde y duplican registros.
- **El modelo puede recuperar el generador.** Como los datos se generaron a partir del mismo tipo de
  señales que miden las features, las métricas de aquí demuestran que el pipeline funciona, no lo bien que se puede predecir el churn
  en el negocio real.
