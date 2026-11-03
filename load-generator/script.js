// Trafic continu vers java-demo. Réglable via les variables d'env VUS et DURATION.
import http from 'k6/http';
import { sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://java-demo:8080';

// Poids relatifs des endpoints
const ENDPOINTS = [
  { path: '/hello', weight: 5 },
  { path: '/slow', weight: 2 },
  { path: '/error', weight: 2 },
  { path: '/order', weight: 3 },
];
const TOTAL = ENDPOINTS.reduce((s, e) => s + e.weight, 0);

export const options = {
  vus: Number(__ENV.VUS || 5),
  duration: __ENV.DURATION || '8760h',
  // Les 500 de /error sont attendues : ne pas faire échouer k6
  thresholds: {},
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)'],
};

function pick() {
  let r = Math.random() * TOTAL;
  for (const e of ENDPOINTS) {
    if ((r -= e.weight) < 0) return e.path;
  }
  return ENDPOINTS[0].path;
}

export default function () {
  http.get(`${BASE_URL}${pick()}`, { tags: { name: 'java-demo' } });
  sleep(Math.random() * 0.5 + 0.1);
}
