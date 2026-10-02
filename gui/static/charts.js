/* Small SVG charts for the quantum tab: columns (plain, grouped or stacked), a
   line chart with a crosshair, and a horizontal dot plot. Colours come from the
   CSS tokens (--series-*, --grid, --axis), so light and dark mode are each
   their own validated steps, never a flip. Every mark has a hover tooltip. */
'use strict';

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, String(v)));
  if (text !== undefined) node.textContent = text;
  return node;
}

function fmt(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const d = digits === undefined ? 3 : digits;
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString();
  if (Number.isInteger(value)) return String(value);
  return Number(value.toPrecision(d)).toString();
}

function pct(value, digits) {
  if (value === null || value === undefined) return '—';
  const p = 100 * value;
  if (p === 0) return '0%';
  if (p < 0.01) return '<0.01%';
  return `${p.toFixed(digits === undefined ? (p < 1 ? 2 : 1) : digits)}%`;
}

/* Clean axis ticks: 1, 2, 2.5 or 5 times a power of ten. */
function niceTicks(min, max, count) {
  if (min === max) { max = min + 1; }
  const span = max - min;
  const raw = span / Math.max(1, count || 4);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) {
    ticks.push(Math.abs(t) < 1e-12 ? 0 : t);
  }
  return ticks;
}

function chartTooltip(host) {
  let tip = host.querySelector('.chart-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.className = 'chart-tip hidden';
    host.appendChild(tip);
  }
  return {
    show(html, x, y) {
      tip.innerHTML = html;
      tip.classList.remove('hidden');
      const hostRect = host.getBoundingClientRect();
      const left = Math.min(x + 14, hostRect.width - tip.offsetWidth - 4);
      tip.style.left = Math.max(4, left) + 'px';
      tip.style.top = Math.max(0, y - tip.offsetHeight - 8) + 'px';
    },
    hide() { tip.classList.add('hidden'); },
  };
}

function hostPoint(host, event) {
  const rect = host.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* Legend with a swatch per series; text stays in text colours. */
function chartLegend(target, series) {
  target.textContent = '';
  if (series.length < 2) return;
  series.forEach((s) => {
    const key = document.createElement('span');
    key.className = 'legend-key';
    const sw = document.createElement('span');
    sw.className = s.line ? 'swatch-line' : 'swatch';
    sw.style.background = s.color;
    key.append(sw, s.name);
    target.appendChild(key);
  });
}

/* Columns over categories. opts.series = [{name, color}], data = [{label,
   values: [..per series], tip}], stacked or grouped. A marker can flag one
   category (e.g. the optimum). */
function columnChart(host, data, opts) {
  host.querySelectorAll('svg').forEach((n) => n.remove());
  const series = opts.series;
  const width = Math.max(320, host.clientWidth || 600);
  const height = opts.height || 230;
  const pad = { l: 46, r: 10, t: 14, b: 40 };
  const innerW = width - pad.l - pad.r, innerH = height - pad.t - pad.b;
  const totals = data.map((d) => (opts.stacked ? d.values.reduce((a, b) => a + b, 0)
                                              : Math.max(...d.values)));
  const maxY = Math.max(1e-12, ...totals) * 1.08;
  const ticks = niceTicks(0, maxY, 4);
  const top = ticks[ticks.length - 1] || maxY;
  const y = (v) => pad.t + innerH - (v / top) * innerH;
  const band = innerW / Math.max(1, data.length);
  const groupW = Math.min(opts.stacked ? 24 : 24 * series.length, band - 2);
  const barW = opts.stacked ? groupW : Math.max(2, groupW / series.length - 1);

  const svg = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img',
                             'aria-label': opts.label || 'chart' });
  ticks.forEach((t) => {
    svg.appendChild(svgEl('line', { x1: pad.l, x2: width - pad.r, y1: y(t), y2: y(t),
                                    class: t === 0 ? 'axis-line' : 'grid-line' }));
    svg.appendChild(svgEl('text', { x: pad.l - 6, y: y(t) + 4, class: 'tick', 'text-anchor': 'end' },
                          opts.percent ? pct(t, Number.isInteger(Math.round(t * 1e6) / 1e4) ? 0 : 1) : fmt(t)));
  });
  const every = Math.max(1, Math.ceil(data.length / Math.floor(innerW / 34)));
  const tip = chartTooltip(host);
  data.forEach((d, i) => {
    const cx = pad.l + band * i + band / 2;
    if (i % every === 0 || d.mark) {
      svg.appendChild(svgEl('text', { x: cx, y: height - pad.b + 14, class: 'tick' + (d.mark ? ' tick-strong' : ''),
                                      'text-anchor': 'middle' }, d.label));
    }
    if (d.mark) {
      svg.appendChild(svgEl('text', { x: cx, y: height - pad.b + 28, class: 'tick tick-strong',
                                      'text-anchor': 'middle' }, d.mark));
    }
    let base = 0;
    d.values.forEach((v, k) => {
      if (!v) return;
      const x0 = opts.stacked ? cx - barW / 2 : cx - groupW / 2 + k * (barW + 1);
      const y1 = y(opts.stacked ? base + v : v);
      const y0 = y(opts.stacked ? base : 0);
      const h = Math.max(1, y0 - y1 - (opts.stacked && base > 0 ? 2 : 0));
      const r = Math.min(4, barW / 2, h);
      // rounded data end, square at the baseline
      const path = `M${x0},${y0}V${y0 - h + r}Q${x0},${y0 - h} ${x0 + r},${y0 - h}` +
                   `H${x0 + barW - r}Q${x0 + barW},${y0 - h} ${x0 + barW},${y0 - h + r}V${y0}Z`;
      svg.appendChild(svgEl('path', { d: path, fill: series[k].color }));
      base += v;
    });
    const hit = svgEl('rect', { x: pad.l + band * i, y: pad.t, width: band, height: innerH,
                                fill: 'transparent', class: 'hit' });
    hit.addEventListener('mousemove', (e) => {
      const [px, py] = hostPoint(host, e);
      tip.show(d.tip || `<strong>${escapeHtml(d.label)}</strong><br>` + series.map((s, k) =>
        `${escapeHtml(s.name)}: ${opts.percent ? pct(d.values[k]) : fmt(d.values[k])}`).join('<br>'), px, py);
    });
    hit.addEventListener('mouseleave', () => tip.hide());
    svg.appendChild(hit);
  });
  if (opts.xLabel) {
    svg.appendChild(svgEl('text', { x: pad.l + innerW / 2, y: height - 2, class: 'axis-title',
                                    'text-anchor': 'middle' }, opts.xLabel));
  }
  host.insertBefore(svg, host.firstChild);
  if (opts.legend) chartLegend(opts.legend, series);
}

/* Lines over a numeric x. series = [{name, color, points: [[x, y, extra]]}],
   refs = [{y, label}] drawn as hairlines with a direct label. */
function lineChart(host, series, opts) {
  host.querySelectorAll('svg').forEach((n) => n.remove());
  const width = Math.max(320, host.clientWidth || 600);
  const height = opts.height || 220;
  const pad = { l: 50, r: 70, t: 12, b: 34 };
  const innerW = width - pad.l - pad.r, innerH = height - pad.t - pad.b;
  const xs = series.flatMap((s) => s.points.map((p) => p[0]));
  const ys = series.flatMap((s) => s.points.map((p) => p[1]))
    .concat((opts.refs || []).map((r) => r.y)).filter((v) => v !== null && v !== undefined);
  if (!xs.length) return;
  const minX = Math.min(...xs), maxX = Math.max(...xs, minX + 1);
  let minY = Math.min(...ys), maxY = Math.max(...ys);
  const padY = (maxY - minY || 1) * 0.06;
  minY -= padY; maxY += padY;
  const ticks = niceTicks(minY, maxY, 4);
  const x = (v) => pad.l + ((v - minX) / (maxX - minX)) * innerW;
  const y = (v) => pad.t + innerH - ((v - minY) / (maxY - minY)) * innerH;

  const svg = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img',
                             'aria-label': opts.label || 'line chart' });
  ticks.forEach((t) => {
    if (t < minY || t > maxY) return;
    svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + innerW, y1: y(t), y2: y(t), class: 'grid-line' }));
    svg.appendChild(svgEl('text', { x: pad.l - 6, y: y(t) + 4, class: 'tick', 'text-anchor': 'end' }, fmt(t)));
  });
  niceTicks(minX, maxX, 5).forEach((t) => {
    if (t < minX || t > maxX) return;
    svg.appendChild(svgEl('text', { x: x(t), y: height - pad.b + 16, class: 'tick', 'text-anchor': 'middle' }, fmt(t)));
  });
  svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + innerW, y1: pad.t + innerH, y2: pad.t + innerH, class: 'axis-line' }));
  (opts.refs || []).forEach((r) => {
    if (r.y === null || r.y === undefined) return;
    svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + innerW, y1: y(r.y), y2: y(r.y), class: 'ref-line' }));
    svg.appendChild(svgEl('text', { x: pad.l + innerW + 4, y: y(r.y) + 4, class: 'tick' }, r.label));
  });
  series.forEach((s) => {
    if (!s.points.length) return;
    const d = s.points.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
    svg.appendChild(svgEl('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2,
                                    'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    const last = s.points[s.points.length - 1];
    svg.appendChild(svgEl('circle', { cx: x(last[0]), cy: y(last[1]), r: 4, fill: s.color, class: 'end-dot' }));
  });
  if (opts.xLabel) {
    svg.appendChild(svgEl('text', { x: pad.l + innerW / 2, y: height - 2, class: 'axis-title',
                                    'text-anchor': 'middle' }, opts.xLabel));
  }
  // crosshair + tooltip
  const cross = svgEl('line', { y1: pad.t, y2: pad.t + innerH, class: 'crosshair hidden' });
  svg.appendChild(cross);
  const tip = chartTooltip(host);
  const hit = svgEl('rect', { x: pad.l, y: pad.t, width: innerW, height: innerH, fill: 'transparent' });
  hit.addEventListener('mousemove', (e) => {
    const [px, py] = hostPoint(host, e);
    const svgX = e.clientX - svg.getBoundingClientRect().left;
    const value = minX + ((svgX - pad.l) / innerW) * (maxX - minX);
    const rows = series.map((s) => {
      let best = s.points[0];
      s.points.forEach((p) => { if (Math.abs(p[0] - value) < Math.abs(best[0] - value)) best = p; });
      return [s, best];
    });
    const at = rows[0][1][0];
    cross.setAttribute('x1', x(at)); cross.setAttribute('x2', x(at));
    cross.classList.remove('hidden');
    tip.show(`<strong>${escapeHtml(opts.xName || 'x')} ${fmt(at)}</strong><br>` + rows.map(([s, p]) =>
      `${escapeHtml(s.name)}: ${fmt(p[1], 4)}${p[2] ? ' · ' + escapeHtml(p[2]) : ''}`).join('<br>'), px, py);
  });
  hit.addEventListener('mouseleave', () => { cross.classList.add('hidden'); tip.hide(); });
  svg.appendChild(hit);
  host.insertBefore(svg, host.firstChild);
  if (opts.legend) chartLegend(opts.legend, series.map((s) => ({ ...s, line: true })));
}

/* Horizontal dot plot: one row per run, value on a shared axis, with a
   reference line (e.g. the proven optimum). */
function dotPlot(host, rows, opts) {
  host.querySelectorAll('svg').forEach((n) => n.remove());
  if (!rows.length) return;
  const width = Math.max(320, host.clientWidth || 600);
  const rowH = 28;
  const pad = { l: Math.min(300, Math.max(140, 7 * Math.max(...rows.map((r) => r.label.length)))), r: 30, t: 10, b: 34 };
  const height = pad.t + pad.b + rowH * rows.length;
  const values = rows.map((r) => r.value).filter((v) => v !== null && v !== undefined);
  if (opts.ref !== undefined && opts.ref !== null) values.push(opts.ref);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (lo === hi) { lo -= 1; hi += 1; }
  lo = Math.floor(lo - 0.5); hi = Math.ceil(hi + 0.5);
  const innerW = width - pad.l - pad.r;
  const x = (v) => pad.l + ((v - lo) / (hi - lo)) * innerW;
  const svg = svgEl('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img',
                             'aria-label': opts.label || 'dot plot' });
  niceTicks(lo, hi, 6).forEach((t) => {
    svg.appendChild(svgEl('line', { x1: x(t), x2: x(t), y1: pad.t, y2: height - pad.b, class: 'grid-line' }));
    svg.appendChild(svgEl('text', { x: x(t), y: height - pad.b + 16, class: 'tick', 'text-anchor': 'middle' }, fmt(t)));
  });
  if (opts.ref !== undefined && opts.ref !== null) {
    svg.appendChild(svgEl('line', { x1: x(opts.ref), x2: x(opts.ref), y1: pad.t - 4, y2: height - pad.b, class: 'ref-line' }));
  }
  if (opts.xLabel) {
    svg.appendChild(svgEl('text', { x: pad.l + innerW / 2, y: height - 2, class: 'axis-title', 'text-anchor': 'middle' }, opts.xLabel));
  }
  const tip = chartTooltip(host);
  rows.forEach((r, i) => {
    const cy = pad.t + rowH * i + rowH / 2;
    svg.appendChild(svgEl('text', { x: pad.l - 10, y: cy + 4, class: 'row-label', 'text-anchor': 'end' }, r.label));
    svg.appendChild(svgEl('line', { x1: pad.l, x2: pad.l + innerW, y1: cy, y2: cy, class: 'grid-line' }));
    if (r.value === null || r.value === undefined) {
      svg.appendChild(svgEl('text', { x: pad.l + 4, y: cy + 4, class: 'tick' }, r.empty || 'no plan'));
      return;
    }
    const dot = svgEl('circle', { cx: x(r.value), cy, r: 5, fill: r.color || 'var(--series-1)', class: 'end-dot' });
    svg.appendChild(dot);
    svg.appendChild(svgEl('text', { x: x(r.value) + 10, y: cy + 4, class: 'tick tick-strong' }, fmt(r.value)));
    const hit = svgEl('rect', { x: 0, y: cy - rowH / 2, width, height: rowH, fill: 'transparent' });
    hit.addEventListener('mousemove', (e) => {
      const [px, py] = hostPoint(host, e);
      tip.show(r.tip || `<strong>${escapeHtml(r.label)}</strong><br>${fmt(r.value)}`, px, py);
    });
    hit.addEventListener('mouseleave', () => tip.hide());
    svg.appendChild(hit);
  });
  host.insertBefore(svg, host.firstChild);
}
