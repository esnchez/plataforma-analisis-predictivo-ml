// Load test for the read path: GET /customers/{id}/churn -> DynamoDB GetItem.
// Targets from the README: 500 req/s sustained, p99 < 150 ms, error rate < 0.5%,
// saturation (latency degrading) from about 1,500 req/s.
//
//   docker compose --profile loadtest run --rm loadtest                       steady 500 req/s, 5 min
//   SCENARIO=ramp docker compose --profile loadtest run --rm loadtest         ramp to find saturation
//   k6 run loadtest/k6.js                                                     k6 installed locally
//
// In Compose, k6 streams its metrics to Prometheus; watch them live in Grafana (http://localhost:3000).
//
// Env: SCENARIO  steady (default) | ramp
//      RATE      steady: req/s (default 500)          MAX_RATE  ramp: final req/s (default 2000)
//      DURATION  steady: length (default 5m)          RAMP_DURATION  ramp: length (default 5m)
//      BASE_URL  (default http://localhost:8000)      CUSTOMERS  ID range to sample (default 2000)
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const CUSTOMERS = parseInt(__ENV.CUSTOMERS || '2000');
const SCENARIO = __ENV.SCENARIO || 'steady';

// Cancelled customers have no score, so 404 is a valid answer, not an error.
http.setResponseCallback(http.expectedStatuses(200, 404));

const scenarios = {
  // Fixed arrival rate: measures the req/s target directly instead of depending on response time.
  steady: {
    executor: 'constant-arrival-rate',
    rate: parseInt(__ENV.RATE || '500'),
    timeUnit: '1s',
    duration: __ENV.DURATION || '5m',
    preAllocatedVUs: 100,
    maxVUs: 1000,
  },
  // Arrival rate rising linearly. The saturation point is where the served rate stops following
  // the target (k6 starts dropping iterations) and latency climbs; read it in Grafana.
  ramp: {
    executor: 'ramping-arrival-rate',
    startRate: 50,
    timeUnit: '1s',
    stages: [{ target: parseInt(__ENV.MAX_RATE || '2000'), duration: __ENV.RAMP_DURATION || '5m' }],
    preAllocatedVUs: 100,
    maxVUs: 2000,
  },
};

export const options = {
  scenarios: { [SCENARIO]: scenarios[SCENARIO] },
  // The steady test pass/fails on the targets. The ramp test is meant to exceed them, so it only reports.
  thresholds: SCENARIO === 'steady'
    ? { http_req_duration: ['p(50)<50', 'p(95)<100', 'p(99)<150'], http_req_failed: ['rate<0.005'] }
    : {},
};

export default function () {
  const id = `C-${String(Math.floor(Math.random() * CUSTOMERS) + 1).padStart(5, '0')}`;
  const res = http.get(`${BASE_URL}/customers/${id}/churn`, { tags: { name: 'customer_churn' } });
  check(res, { 'status is 200 or 404': (r) => r.status === 200 || r.status === 404 });
}
