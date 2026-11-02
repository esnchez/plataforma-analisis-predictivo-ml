// Churn risk frontend. Calls the API through the nginx /api proxy; read-only.
// All API values go into the DOM through textContent / attributes, never innerHTML.
'use strict';

const API = '/api';
const BANDS = { high: 'High risk', medium: 'Medium risk', low: 'Low risk' };
const FEATURES = {
  tenure_months: ['Tenure', (v) => `${v} months`],
  plan_tier: ['Plan', (v) => ['Basic', 'Pro', 'Enterprise'][v] ?? v],
  is_annual: ['Contract', (v) => (v ? 'Annual' : 'Monthly')],
  seats: ['Paid seats', (v) => `${v}`],
  months_to_renewal: ['Months to renewal', (v) => `${v}`],
  logins_1m: ['Logins last month', (v) => `${v}`],
  logins_3m_avg: ['Avg monthly logins (3 mo)', (v) => v.toFixed(0)],
  logins_trend: ['Login trend vs previous 3 mo', (v) => `×${v.toFixed(2)}`],
  active_seat_ratio_1m: ['Seats in use last month', pct],
  active_seat_ratio_3m_avg: ['Seats in use (3 mo avg)', pct],
  feature_events_3m: ['Feature usage events (3 mo)', (v) => `${v}`],
  tickets_3m: ['Support tickets (3 mo)', (v) => `${v}`],
  high_sev_tickets_3m: ['High-severity tickets (3 mo)', (v) => `${v}`],
  payment_failures_3m: ['Failed payments (3 mo)', (v) => `${v}`],
};
const SERIES = [
  { key: 'precision', label: 'Precision', color: '--series-1' },
  { key: 'recall', label: 'Recall', color: '--series-2' },
];

function pct(v, digits = 0) { return v == null ? '–' : `${(v * 100).toFixed(digits)}%`; }
function featureLabel(name) { return FEATURES[name]?.[0] ?? name; }
function featureValue(name, value) { const f = FEATURES[name]; return f ? f[1](value) : String(value); }

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

async function api(path) {
  const response = await fetch(API + path);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function bandBadge(band) {
  return el('span', { class: `band band-${band}` }, el('span', { class: 'band-icon', 'aria-hidden': 'true' }),
    el('span', { text: BANDS[band] ?? band }));
}

function meter(probability) {
  const fill = el('div', { class: 'meter-fill' });
  fill.style.width = `${probability * 100}%`;
  return el('div', { class: 'meter' },
    el('div', { class: 'meter-track', role: 'img', 'aria-label': pct(probability) }, fill),
    el('span', { class: 'meter-value', text: pct(probability) }));
}

// ---- Summary tiles and header ------------------------------------------------------------------

async function loadSummary() {
  const summary = await api('/churn/summary');
  const tiles = document.getElementById('tiles');
  tiles.replaceChildren(
    el('div', { class: 'tile' }, el('div', { class: 'tile-label', text: 'Active customers scored' }),
      el('div', { class: 'tile-value', text: summary.customers.toLocaleString() })),
    ...Object.keys(BANDS).map((band) => el('div', { class: 'tile' },
      el('div', { class: 'tile-label' }, bandBadge(band)),
      el('div', { class: 'tile-value', text: summary.by_band[band].toLocaleString() }),
      el('div', { class: 'tile-sub', text: `${pct(summary.by_band[band] / Math.max(1, summary.customers))} of customers` }))),
  );
}

function setMeta(item) {
  if (!item) return;
  const scored = new Date(item.scored_at).toLocaleString();
  document.getElementById('meta').textContent =
    `Data as of ${item.snapshot_month} · model ${item.model_version} · scored ${scored}`;
}

// ---- At-risk table --------------------------------------------------------------------------------

let selectedRow = null;

async function loadAtRisk(band) {
  for (const tab of document.querySelectorAll('#band-tabs button')) {
    tab.setAttribute('aria-selected', String(tab.dataset.band === band));
  }
  const body = document.querySelector('#at-risk-table tbody');
  body.style.opacity = '0.5';  // keep the previous rows while refetching
  const { customers } = await api(`/churn/at-risk?band=${band}&limit=50`);
  body.style.opacity = '';
  setMeta(customers[0]);
  if (!customers.length) {
    body.replaceChildren(el('tr', {}, el('td', { class: 'empty', colspan: '3', text: 'No customers in this band.' })));
    return;
  }
  body.replaceChildren(...customers.map((c) => {
    const row = el('tr', { class: 'clickable', tabindex: '0' },
      el('td', { class: 'num', text: c.CustomerID }),
      el('td', {}, meter(c.churn_probability)),
      el('td', { class: 'factors', text: c.top_factors.map((f) => featureLabel(f.feature)).join(' · ') || '–' }));
    const open = () => {
      selectedRow?.classList.remove('selected');
      selectedRow = row;
      row.classList.add('selected');
      showCustomer(c.CustomerID);
    };
    row.addEventListener('click', open);
    row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    return row;
  }));
}

// ---- Customer detail ----------------------------------------------------------------------------

async function showCustomer(customerId) {
  const card = document.getElementById('customer-card');
  card.hidden = false;
  history.replaceState(null, '', `#customer=${encodeURIComponent(customerId)}`);  // shareable link
  const item = await api(`/customers/${encodeURIComponent(customerId)}/churn`);
  const close = el('button', { class: 'close', type: 'button', text: 'Close', onclick: () => {
    card.hidden = true;
    history.replaceState(null, '', location.pathname);
  } });
  if (!item) {
    card.replaceChildren(close, el('h2', { text: customerId }),
      el('p', { class: 'subtitle', text: 'No active churn score: the customer does not exist or has already cancelled.' }));
    return;
  }
  const maxImpact = Math.max(...item.top_factors.map((f) => f.impact), 0.0001);
  const factors = item.top_factors.length
    ? el('ul', { class: 'factor-list' }, ...item.top_factors.map((f) => {
      const bar = el('div', { class: 'factor-bar', role: 'img', 'aria-label': `impact ${f.impact.toFixed(2)}` });
      bar.style.width = `${(f.impact / maxImpact) * 100}%`;
      return el('li', {},
        el('div', { class: 'factor-head' }, el('span', { text: featureLabel(f.feature) }),
          el('span', { class: 'factor-value', text: featureValue(f.feature, f.value) })),
        bar);
    }))
    : el('p', { class: 'subtitle', text: 'No feature raises this customer’s risk above the baseline.' });

  card.replaceChildren(close,
    el('h2', { text: `Customer ${item.CustomerID}` }),
    el('div', { class: 'customer' },
      el('div', {},
        el('div', { class: 'hero', text: pct(item.churn_probability) }),
        el('div', { class: 'subtitle', text: `chance of cancelling in the next ${item.horizon_months} months` }),
        el('div', { style: 'margin-top:8px' }, bandBadge(item.risk_band)),
        el('dl', { class: 'kv' },
          el('dt', { text: 'Data as of' }), el('dd', { text: item.snapshot_month }),
          el('dt', { text: 'Model' }), el('dd', { text: item.model_version }),
          el('dt', { text: 'Scored' }), el('dd', { text: new Date(item.scored_at).toLocaleString() }))),
      el('div', {},
        el('h3', { class: 'subtitle', text: 'What raises the risk most (longer bar = stronger effect)' }),
        factors)));
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ---- Model performance chart --------------------------------------------------------------------

let perfMonths = [];

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function renderLegend() {
  document.getElementById('perf-legend').replaceChildren(...SERIES.map((s) => {
    const key = el('span', { class: 'line-key' });
    key.style.background = `var(${s.color})`;
    return el('span', { class: 'legend-item' }, key, el('span', { text: s.label }));
  }));
}

function renderChart() {
  const container = document.getElementById('perf-chart');
  if (!perfMonths.length) { container.replaceChildren(); return; }
  const width = Math.max(320, container.clientWidth);
  const height = 240;
  const m = { top: 12, right: 110, bottom: 28, left: 40 };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const n = perfMonths.length;
  const x = (i) => m.left + (n === 1 ? w / 2 : (i / (n - 1)) * w);
  const y = (v) => m.top + (1 - v) * h;

  const root = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', tabindex: '0',
    'aria-label': 'High-band precision and recall per month; the table view below lists every value. Use arrow keys to inspect months.' });

  for (const t of [0, 0.25, 0.5, 0.75, 1]) {
    root.append(svg('line', { x1: m.left, x2: m.left + w, y1: y(t), y2: y(t), stroke: cssVar(t === 0 ? '--axis' : '--grid'), 'stroke-width': 1 }));
    const label = svg('text', { x: m.left - 8, y: y(t) + 4, 'text-anchor': 'end' });
    label.textContent = pct(t);
    root.append(label);
  }
  const every = Math.ceil(n / Math.max(1, Math.floor(w / 70)));
  perfMonths.forEach((month, i) => {
    if (i % every !== 0 && i !== n - 1) return;
    const label = svg('text', { x: x(i), y: height - 8, 'text-anchor': 'middle' });
    label.textContent = month.snapshot_month;
    root.append(label);
  });

  const surface = cssVar('--surface');
  const ends = [];
  for (const s of SERIES) {
    const color = cssVar(s.color);
    const points = perfMonths.map((mo, i) => [x(i), mo.high_band[s.key]]).filter(([, v]) => v != null);
    root.append(svg('path', { d: points.map(([px, v], i) => `${i ? 'L' : 'M'}${px},${y(v)}`).join(' '),
      fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    for (const [px, v] of points) {
      root.append(svg('circle', { cx: px, cy: y(v), r: 4, fill: color, stroke: surface, 'stroke-width': 2 }));
    }
    const [lx, lv] = points[points.length - 1];
    ends.push({ x: lx, y: y(lv), text: `${s.label} ${pct(lv)}` });
  }
  // Direct end labels only when they don't collide; otherwise the legend and tooltip carry identity.
  if (ends.length < 2 || Math.abs(ends[0].y - ends[1].y) >= 14) {
    for (const e of ends) {
      const label = svg('text', { x: e.x + 10, y: e.y + 4, class: 'end-label' });
      label.textContent = e.text;
      root.append(label);
    }
  }

  // Crosshair: snaps to the nearest month; the whole plot area is the hit target.
  const cross = svg('line', { y1: m.top, y2: m.top + h, stroke: cssVar('--axis'), 'stroke-width': 1, visibility: 'hidden' });
  const hit = svg('rect', { x: m.left - 10, y: m.top, width: w + 20, height: h, fill: 'transparent' });
  root.append(cross, hit);
  let active = null;
  const show = (i, clientX, clientY) => {
    active = i;
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    showTooltip(perfMonths[i], clientX, clientY);
  };
  const hide = () => { cross.setAttribute('visibility', 'hidden'); hideTooltip(); active = null; };
  hit.addEventListener('pointermove', (e) => {
    const box = root.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    const i = n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - m.left) / w) * (n - 1))));
    show(i, e.clientX, e.clientY);
  });
  hit.addEventListener('pointerleave', hide);
  root.addEventListener('blur', hide);
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = active == null ? n - 1 : Math.max(0, Math.min(n - 1, active + (e.key === 'ArrowRight' ? 1 : -1)));
    const box = root.getBoundingClientRect();
    show(i, box.left + (x(i) / width) * box.width, box.top + (m.top / height) * box.height);
  });
  container.replaceChildren(root);
}

function showTooltip(month, clientX, clientY) {
  const tip = document.getElementById('tooltip');
  tip.replaceChildren(
    el('div', { class: 'tooltip-title', text: `${month.snapshot_month}${month.backtest ? ' (backtest)' : ''}` }),
    ...SERIES.map((s) => {
      const key = el('span', { class: 'line-key' });
      key.style.background = `var(${s.color})`;
      return el('div', { class: 'tooltip-row' }, key, el('strong', { text: pct(month.high_band[s.key]) }), el('span', { text: s.label }));
    }),
    el('div', { class: 'tooltip-row' }, el('span', { text: `${month.high_band.flagged} flagged · ${month.positives} churned` })));
  tip.hidden = false;
  const left = Math.min(clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${clientY + 14}px`;
}

function hideTooltip() { document.getElementById('tooltip').hidden = true; }

async function loadPerformance() {
  const report = await api('/model/performance');
  const note = document.getElementById('perf-note');
  if (!report) {
    note.textContent = 'No performance report yet: the monitoring job has not run.';
    return;
  }
  perfMonths = report.months;
  renderLegend();
  renderChart();
  const notes = [];
  if (perfMonths.some((mo) => mo.backtest)) {
    notes.push('Backtest months: models retrained with only the data available at the time, as if the system had been running then.');
  }
  if (report.pending_months.length) {
    notes.push(`Waiting for outcomes (${report.horizon_months}-month window still open): ${report.pending_months.join(', ')}.`);
  }
  note.textContent = notes.join(' ');
  document.querySelector('#perf-table tbody').replaceChildren(...perfMonths.map((mo) => el('tr', {},
    el('td', { text: mo.snapshot_month }),
    el('td', { text: mo.model_version }),
    el('td', { text: mo.customers_predicted.toLocaleString() }),
    el('td', { text: `${mo.positives} (${pct(mo.base_rate, 1)})` }),
    el('td', { text: pct(mo.high_band.precision, 1) }),
    el('td', { text: pct(mo.high_band.recall, 1) }),
    el('td', { text: mo.roc_auc?.toFixed(3) ?? '–' }),
    el('td', { text: mo.pr_auc?.toFixed(3) ?? '–' }))));
}

// ---- Wiring -------------------------------------------------------------------------------------

document.getElementById('band-tabs').addEventListener('click', (e) => {
  const band = e.target.closest('button')?.dataset.band;
  if (band) loadAtRisk(band);
});
document.getElementById('lookup-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const id = document.getElementById('lookup-input').value.trim().toUpperCase();
  if (id) showCustomer(id);
});
let resizeTimer;
window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 100); });
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', renderChart);

const linked = new URLSearchParams(location.hash.slice(1)).get('customer');
if (linked) showCustomer(linked);

Promise.all([loadSummary(), loadAtRisk('high'), loadPerformance()]).catch((err) => {
  document.getElementById('meta').textContent = `Could not reach the API: ${err.message}`;
});
