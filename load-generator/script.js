// Trafic continu vers les instances java-demo. Réglable via VUS, DURATION et BASE_URLS.
import http from 'k6/http';
import { sleep } from 'k6';

// Liste séparée par des virgules : une requête part vers chaque instance à chaque itération
const BASE_URLS = (__ENV.BASE_URLS || 'http://java-demo:8080').split(',');

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
  const requests = BASE_URLS.map((base) => ['GET', `${base}${pick()}`, null, { tags: { name: 'java-demo' } }]);
  http.batch(requests);
  sleep(Math.random() * 0.5 + 0.1);
}
