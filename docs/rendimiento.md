# Rendimiento

Resultados de las pruebas de carga, el punto de saturación en local y qué limita la API. Cómo ejecutar las pruebas y
leer el dashboard: [ejecucion-local.md](ejecucion-local.md#prueba-de-carga-y-grafana).

Todas las mediciones se hicieron el 2026-10-31 en un portátil Windows con Docker Desktop. Su VM Linux tiene
**8 CPUs, compartidas por todos los contenedores**: k6, la API, dynamodb-local, LocalStack, Prometheus y Grafana.

## Resumen

| Objetivo del README | En local | Veredicto |
|---|---|---|
| Tasa de errores < 0.5% | 0% en todas las ejecuciones, incluida la sobrecarga | ✅ Cumplido |
| p99 < 150 ms | Cumplido hasta ~100 req/s constantes (p99 65 ms) | ⚠️ Cumplido solo con tasas bajas |
| 500 req/s sostenidas | El throughput se queda en ~190–235 req/s | ❌ No cumplido en local |
| Saturación en torno a 1,500 req/s | La latencia se degrada a partir de ~100 req/s | ❌ Mucho más baja en local |

Las cifras bajas tienen cuatro causas (ver [por qué se satura la API](#por-qué-la-api-se-satura-pronto)). Dos
solo existen en local: el emulador lento y el portátil compartido. Dos acompañarían a la API a producción:
la CPU que cuesta cada petición y la contención de threads. Los objetivos reales solo se pueden verificar contra DynamoDB
real ([produccion.md](produccion.md#probar-los-objetivos-de-latencia-contra-dynamodb-real)), y las
correcciones de la API que se indican abajo deberían ir primero.

## Throughput del emulador

`GetItem` medido directamente, sin la API, sobre 2,000 elementos:

| Clientes concurrentes | LocalStack 4.4 | dynamodb-local 2.5.2 |
|---|---|---|
| 1 | 18/s, p99 74 ms | 20/s, p99 92 ms |
| 8 | 93/s, p99 213 ms | 152/s, p99 87 ms |
| 32 | 90/s, p99 914 ms | 268/s, p99 238 ms |

Por eso se eligió dynamodb-local (decisión D24). Aun así, tarda ~45 ms por consulta sin
carga. Un viaje de ida y vuelta HTTP sin más hasta él tarda solo 2–4 ms dentro de Docker, así que el resto es el propio
emulador.

## Tasas constantes

k6 a una tasa fija durante 40 s, con la CPU de la API leída del contador de CPU de su contenedor:

| Tasa | p99 | Descartadas | CPU de la API | CPU por petición |
|---|---|---|---|---|
| 25 req/s | 57 ms | 0 | 31% | 12.5 ms |
| 50 req/s | 55 ms | 0 | 52% | 10.4 ms |
| **100 req/s** | **65 ms ✅** | 0 | 101% | 10.1 ms |
| 150 req/s | 0.6–1.06 s ❌ | 0–59 | 239% | 16.1 ms |
| 500 req/s (objetivo del README) | 4–10 s ❌ | ~70% de las peticiones | ~450% | — |

A 100 req/s la API cumple el objetivo de latencia. A 150 req/s no, aunque el contenedor
usa solo el 239% del 400% que podrían usar sus 4 workers.

## Rampa (punto de saturación)

`SCENARIO=ramp MAX_RATE=400 RAMP_DURATION=3m`, leído de los histogramas de la propia API en Prometheus.
Una de dos ejecuciones:

| Tiempo transcurrido en la prueba | Servidas | Descartadas | p99 de consulta de la API | p99 de DynamoDB |
|---|---|---|---|---|
| 0:25 | 47 req/s | 0 | 108 ms | 64 ms |
| 0:40 | 94 req/s | 0 | 1.6 s | 76 ms |
| 1:10–1:55 | 150–233 req/s | 0 | ~1 s | ~140 ms |
| 2:10 | 226 req/s | 18/s | 2.4 s | 238 ms |
| 2:40+ | ~200 req/s | 80–166/s | 9+ s (los scrapes empiezan a fallar) | 431 ms |

- La rampa supera los 150 ms antes que la prueba constante, porque la carga sigue subiendo antes de que
  las colas puedan vaciarse.
- El throughput máximo fue de ~190 req/s en la otra ejecución. Cuenta con una variación de alrededor de ±20% entre ejecuciones en un portátil compartido.
- **Todas las ejecuciones tuvieron 0 errores:** con sobrecarga el sistema se vuelve más lento pero responde a todo.

## Por qué la API se satura pronto

Unas 100–150 req/s es poco para un endpoint que lee una sola clave. Las mediciones aíslan cuatro causas.

### 1. Cada petición cuesta ~10 ms de CPU, y un worker solo puede usar un núcleo

| Medido | CPU por llamada |
|---|---|
| Un `GetItem` de DynamoDB con boto3 (interfaz resource o client, sin diferencia) | 3.5–5 ms |
| Solo el framework web (`GET /health` no hace nada) | ~3 ms |
| Una consulta completa en el contenedor con poca carga | ~10 ms |

El global interpreter lock (GIL) de Python permite que un proceso ejecute código Python en un solo núcleo a la vez,
sea cual sea el número de threads. Medido directamente, 32 threads llamando a boto3 en un proceso llegan como máximo a
~100% de un núcleo y ~230 llamadas/s. A 10 ms por petición, **un worker no puede superar ~100 req/s**,
así que 4 workers no pueden superar ~400 req/s.

Parte del coste del framework se puede evitar:

- La imagen ejecuta el parser HTTP de uvicorn escrito en Python puro (`h11`) y el bucle asyncio estándar, porque
  `uvloop` y `httptools` no están instalados.
- El middleware de métricas usa `@app.middleware` de Starlette (`BaseHTTPMiddleware`), que añade
  sobrecarga a cada petición.

### 2. El coste por petición crece con la carga (contención)

A 150 req/s cada worker usó de media solo el 54–62% de un núcleo (medido por proceso), y aun así el p99 superó los 0.6 s,
y la CPU por petición subió de 10 ms a 16–24 ms.

Los endpoints son síncronos, así que cada petición se ejecuta en uno de hasta 40 threads por worker. Esos
threads compiten con el event loop por el GIL. Con carga, el event loop, que acepta conexiones
y envía respuestas, espera su turno, así que las respuestas se encolan mientras todavía queda CPU libre. Los
cambios de contexto adicionales también consumen CPU.

Las métricas multiproceso de Prometheus no son la causa: una ejecución comparativa sin ellas no fue
más rápida.

### 3. El emulador hace que cada petición retenga un thread durante más tiempo (solo en local)

dynamodb-local responde en ~45 ms, mientras que DynamoDB real tarda unos pocos ms. Por tanto, cada petición ocupa
un thread unas 10× más tiempo que en producción. Eso significa más peticiones en curso, más threads y
más contención (causa 2).

### 4. Todo comparte un portátil (solo en local)

En el pico, los contenedores usaron el 732% del 800% de la VM. Solo LocalStack consumió el 107–136% durante las pruebas de carga,
aunque las consultas nunca tocan S3. Una ejecución a 150 req/s con LocalStack parado tuvo 0 errores (así que la
API no depende de él), y el p99 bajó de 0.6–1.06 s a 0.47 s.

## Correcciones propuestas

Aún no implementadas. Ordenadas por ganancia esperada respecto al esfuerzo; cada una debería medirse con las mismas
pruebas de tasa fija (100 y 150 req/s) y la rampa.

1. **Cachear las puntuaciones en la API.** Las puntuaciones cambian una vez al mes. Una pequeña caché en memoria por worker (por
   ejemplo de 5 minutos), o cabeceras `Cache-Control` que CloudFront respete, elimina la mayoría de las llamadas a DynamoDB
   y la mayor parte de la CPU de boto3. Probablemente la mayor ganancia individual.
2. **Usar los componentes rápidos del servidor HTTP:** `uvicorn[standard]` instala `uvloop` y `httptools`. Es
   un cambio de una línea en las dependencias.
3. **Sustituir el middleware de métricas** por un middleware ASGI puro: las mismas métricas, menos sobrecarga por
   petición.
4. **Reducir la contención de threads:**
   - reducir el pool de threads, por ejemplo a 8–10 threads por worker; o
   - hacer los endpoints asíncronos con una librería asíncrona de DynamoDB, para que las peticiones no necesiten threads.
5. **Escalar horizontalmente en lugar de verticalmente:** un worker por núcleo, y más instancias en producción. En Lambda,
   la causa 2 desaparece por completo, porque cada instancia atiende una petición a la vez.

## Cómo reproducirlo

```bash
# Tasas fijas, una cada vez
RATE=100 DURATION=40s docker compose --profile loadtest run --rm loadtest
RATE=150 DURATION=40s docker compose --profile loadtest run --rm loadtest

# Rampa
SCENARIO=ramp MAX_RATE=400 RAMP_DURATION=3m docker compose --profile loadtest run --rm loadtest

# CPU usada por el contenedor de la API (microsegundos); léela antes y después de una ejecución
MSYS_NO_PATHCONV=1 docker exec plataforma-analisis-predictivo-ml-api-1 cat /sys/fs/cgroup/cpu.stat
```

`MSYS_NO_PATHCONV=1` solo es necesario en Git Bash en Windows, que de lo contrario reescribe `/sys/...` como
una ruta de Windows.
