/* QUBO view: draw the exported matrix, explain what its columns mean, and score
   a selection of columns — optionally replaying it as a move plan in the main
   player. Shares the helpers and state defined in app.js. */
'use strict';

const qubo = { model: null, canvasCells: null, lastEval: null };

/* Diverging pair from the palette: blue (negative) — neutral — red (positive),
   which is what signed biases call for. */
function quboColor(value, span) {
  const neutral = isDark() ? [56, 56, 53] : [240, 239, 236];
  const negative = isDark() ? [57, 135, 229] : [42, 120, 214];
  const positive = isDark() ? [230, 103, 103] : [208, 59, 59];
  const t = Math.max(-1, Math.min(1, span ? value / span : 0));
  const end = t < 0 ? negative : positive;
  const mix = Math.sqrt(Math.abs(t));                 // keep small biases visible
  const channel = (i) => Math.round(neutral[i] + (end[i] - neutral[i]) * mix);
  return `rgb(${channel(0)},${channel(1)},${channel(2)})`;
}

function drawQuboMatrix() {
  const model = qubo.model;
  if (!model) return;
  const n = model.variables.length;
  const canvas = $('qubo-canvas');
  const host = canvas.parentElement;
  const available = Math.max(240, Math.min(host.clientWidth || 600, 620));
  const cell = Math.max(3, Math.floor(available / n));
  const size = cell * n;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + 'px';
  canvas.style.height = size + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const surface = getComputedStyle(document.body).backgroundColor;
  ctx.fillStyle = isDark() ? '#1a1a19' : '#fcfcfb';
  ctx.fillRect(0, 0, size, size);

  const span = Math.max(Math.abs(model.stats.minCoefficient),
                        Math.abs(model.stats.maxCoefficient)) || 1;
  const grid = new Map();
  model.terms.forEach(([i, j, c]) => {
    grid.set(i * n + j, c);
    ctx.fillStyle = quboColor(c, span);
    ctx.fillRect(j * cell, i * cell, cell, cell);
    if (i !== j) {                                  // mirror for readability
      ctx.fillRect(i * cell, j * cell, cell, cell);
      grid.set(j * n + i, c);
    }
  });

  // where the sequence columns end and the slack columns begin
  const split = model.variables.filter((v) => v.kind === 'sequence').length;
  if (split > 0 && split < n) {
    ctx.strokeStyle = isDark() ? 'rgba(255,255,255,.35)' : 'rgba(0,0,0,.35)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(split * cell + 0.5, 0); ctx.lineTo(split * cell + 0.5, size);
    ctx.moveTo(0, split * cell + 0.5); ctx.lineTo(size, split * cell + 0.5);
    ctx.stroke();
  }
  qubo.canvasCells = { cell, n, grid, size };
  void surface;
}

function quboHover(event) {
  const info = qubo.canvasCells;
  const tip = $('qubo-tip');
  if (!info) return;
  const rect = $('qubo-canvas').getBoundingClientRect();
  const j = Math.floor((event.clientX - rect.left) / info.cell);
  const i = Math.floor((event.clientY - rect.top) / info.cell);
  if (i < 0 || j < 0 || i >= info.n || j >= info.n) { tip.classList.add('hidden'); return; }
  const value = info.grid.get(i * info.n + j);
  const names = qubo.model.variables;
  tip.textContent = i === j
    ? `${names[i].name} — linear ${value !== undefined ? value : 0}`
    : `${names[i].name} × ${names[j].name} — ${value !== undefined ? value : 0}`;
  tip.classList.remove('hidden');
  const host = $('qubo-canvas').parentElement.getBoundingClientRect();
  tip.style.left = Math.min(event.clientX - host.left + 12, host.width - 220) + 'px';
  tip.style.top = (event.clientY - host.top + 12) + 'px';
}

function renderQuboLegend() {
  const legend = $('qubo-legend');
  legend.textContent = '';
  const span = Math.max(Math.abs(qubo.model.stats.minCoefficient),
                        Math.abs(qubo.model.stats.maxCoefficient)) || 1;
  const swatches = el('div', 'legend-swatches');
  [-1, -0.6, -0.25, 0, 0.25, 0.6, 1].forEach((t) => {
    const i = el('i');
    i.style.background = quboColor(t * span, span);
    swatches.appendChild(i);
  });
  const wrap = el('div', 'legend-ramp');
  wrap.appendChild(el('span', null, `${qubo.model.stats.minCoefficient} (reward)`));
  wrap.appendChild(swatches);
  wrap.appendChild(el('span', null, `${qubo.model.stats.maxCoefficient} (penalty)`));
  legend.appendChild(wrap);
  legend.appendChild(el('span', 'hint',
    'diagonal = linear bias · off-diagonal = coupling · the line marks where the '
    + 'sequence columns end and the slack columns begin'));
}

function renderQuboStats() {
  const s = qubo.model.stats;
  const row = $('qubo-stats');
  row.textContent = '';
  row.appendChild(statTile('Columns', String(s.columns),
    `${s.sequenceColumns} sequence + ${s.slackColumns} slack`));
  row.appendChild(statTile('Couplings', String(s.quadraticTerms),
    `${(100 * s.density).toFixed(1)}% of all pairs`));
  row.appendChild(statTile('One-hot groups', String(s.blockingBlocks),
    'one per blocking block'));
  row.appendChild(statTile('Valid selections', s.feasibleAssignments.toLocaleString(),
    `of 2^${s.columns} assignments`));
  row.appendChild(statTile('Bias range',
    `${s.minCoefficient} … ${s.maxCoefficient}`, 'reward … penalty'));
  row.appendChild(statTile('Cheapest per block', String(s.cheapestCost),
    'relocations, before penalties'));
  row.appendChild(statTile('Energy offset', String(s.offset ?? 0),
    'add to an energy for the objective'));
}

function renderQuboInterpretation() {
  const s = qubo.model.stats;
  const box = $('qubo-interpretation');
  box.textContent = '';
  const p = el('p');
  p.innerHTML =
    `Each column is a yes/no decision. <strong>${s.sequenceColumns}</strong> of them are ` +
    '<em>sequence</em> variables <code>x(block, seq)</code> — "block <em>b</em> follows relocation ' +
    `sequence <em>seq</em>" — and <strong>${s.slackColumns}</strong> are slack variables ` +
    '<code>s(…)</code> that turn the model\'s inequalities into the equalities a QUBO can carry.';
  box.appendChild(p);

  const list = el('ul');
  list.appendChild(el('li', null,
    `The ${s.sequenceColumns} sequence columns fall into ${s.blockingBlocks} one-hot groups, ` +
    'one per blocking block: exactly one sequence per block must be chosen. That admits ' +
    `${s.feasibleAssignments.toLocaleString()} selections — everything else among the ` +
    `2^${s.columns} assignments is pushed up by the penalty terms.`));
  list.appendChild(el('li', null,
    `Biases run from ${s.minCoefficient} to ${s.maxCoefficient}: the large negatives reward ` +
    'picking a sequence, the positives punish picking two from one group or two sequences ' +
    'that cannot coexist. Minimising the energy therefore means "pick one cheap sequence per ' +
    'block, and make them compatible".'));
  list.appendChild(el('li', null,
    `Simply taking the cheapest column in each group costs ${s.cheapestCost} relocations, but ` +
    'those sequences generally clash — which is exactly what the couplings encode. Use ' +
    '"Find the best replayable selection" to see what the model really allows.'));
  box.appendChild(list);
}

function renderQuboColumns() {
  const body = $('qubo-vars').querySelector('tbody');
  body.textContent = '';
  $('qubo-column-count').textContent = String(qubo.model.variables.length);
  qubo.model.variables.forEach((v) => {
    const tr = el('tr');
    tr.appendChild(el('td', null, String(v.index)));
    tr.appendChild(el('td', 'mono', v.name));
    tr.appendChild(el('td', null, v.kind));
    tr.appendChild(el('td', null, v.priority === null ? '—' : String(v.priority)));
    tr.appendChild(el('td', null, v.cost === null ? '—'
      : String(v.cost) + (v.complete === false ? ' (provisional)' : '')));
    tr.appendChild(el('td', null, String(v.linear)));
    tr.appendChild(el('td', 'mono', v.relocations.map((r) =>
      r.dst < 0 ? `r${r.period + 1}: out` : `r${r.period + 1}: ${r.src + 1}→${r.dst + 1}`)
      .join('  ') || '—'));
    tr.addEventListener('click', () => {
      $('qubo-selection').value = v.name;
    });
    body.appendChild(tr);
  });
}

async function loadQubo() {
  const name = $('qubo-file').value;
  if (!name) return;
  try {
    const res = await fetch('/api/qubo?path=' + encodeURIComponent(name));
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    qubo.model = data;
    $('qubo-view').classList.remove('hidden');
    $('qubo-file-name').textContent = data.path;
    $('qubo-eval-result').textContent = '';
    renderQuboStats();
    drawQuboMatrix();
    renderQuboLegend();
    renderQuboInterpretation();
    renderQuboColumns();
    $('qubo-match').className = 'pill pill-muted';
    $('qubo-match').textContent = `replays against ${$('instance-select').value || 'no test case'}`;
    $('qubo-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    showError('Could not load the QUBO file: ' + err);
  }
}

async function evaluateQubo(extra) {
  if (!qubo.model) return;
  const box = $('qubo-eval-result');
  box.textContent = 'working…';
  const text = $('qubo-selection').value.trim();
  const payload = {
    path: qubo.model.path,
    instance: $('instance-select').value,
    emptyTiers: numberOrNull($('p-empty-tiers').value),
    maximumHeight: numberOrNull($('p-max-height').value),
    ...(extra || {}),
  };
  if (!extra) {
    if (/^[01\s]+$/.test(text) && text.replace(/\s/g, '').length >= qubo.model.stats.columns) {
      payload.bitstring = text;
    } else {
      payload.selection = text;
    }
  }
  try {
    const res = await fetch('/api/qubo-eval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) { box.textContent = ''; showError(data.error); return; }
    qubo.lastEval = data;
    renderQuboEval(data);
  } catch (err) {
    box.textContent = '';
    showError(String(err));
  }
}

function renderQuboEval(r) {
  const box = $('qubo-eval-result');
  box.textContent = '';
  const match = $('qubo-match');
  if (r.instanceMatches === true) {
    match.className = 'pill pill-good';
    match.textContent = `matches ${r.instance}`;
  } else if (r.instanceMatches === false) {
    match.className = 'pill pill-bad';
    match.textContent = `does not match ${r.instance}`;
  }

  if (r.search) {
    const s = r.search;
    const p = el('p');
    if (s.capped) {
      p.innerHTML = `This model has ${s.total.toLocaleString()} one-per-block selections, ` +
        `more than the ${s.limit.toLocaleString()} this search walks. Evaluate a selection ` +
        'or a bitstring instead.';
    } else if (!s.best) {
      p.innerHTML = `None of the <strong>${s.total.toLocaleString()}</strong> selections ` +
        `replay into a valid plan under a height limit of <strong>${r.heightLimit}</strong> ` +
        'tiers, so this export was made with different <code>-E</code>/<code>-T</code> ' +
        'settings than the parameter panel currently holds. Clear <em>Empty tiers</em> ' +
        '(or match the settings the export used) and search again.';
    } else {
      p.innerHTML = `Of <strong>${s.total.toLocaleString()}</strong> one-per-block selections, ` +
        `<strong>${s.replayable}</strong> replay into a valid plan; the best costs ` +
        `<strong>${s.best.cost}</strong> relocations — that is the optimum this QUBO encodes.`;
    }
    box.appendChild(p);
  }

  if (r.search && !r.search.best) return;      // nothing was selected to describe

  const facts = el('dl', 'kv');
  const add = (k, v) => { facts.appendChild(el('dt', null, k)); facts.appendChild(el('dd', 'mono', v)); };
  add('columns set', r.names.length ? r.names.join('  ') : '(none)');
  add('cost of the sequences', `${r.cost} relocations`);
  add('energy (slacks as given)', String(r.energy));
  add('energy (slacks relaxed)', `${r.energyRelaxed}  — ${r.slacksOn} slack column(s) turned on`);
  if (r.offset !== undefined) {
    add('model objective', `${r.objective}  = energy ${r.energyRelaxed} + offset ${r.offset}`);
  }
  add('one-hot check', r.violations.length ? r.violations.join('; ') : 'one column per block ✓');
  add('height limit applied', `${r.heightLimit} tiers (from the parameter panel)`);
  box.appendChild(facts);

  if (r.unknown && r.unknown.length) {
    box.appendChild(el('p', 'qubo-bad', 'unknown tokens: ' + r.unknown.join(', ')));
  }
  if (r.plan) {
    const line = el('p');
    line.appendChild(el('span', 'qubo-ok',
      `Replays into a valid ${r.plan.relocations.length}-relocation plan. `));
    const button = el('button', 'btn btn-primary', 'Show this plan in the player');
    button.type = 'button';
    button.addEventListener('click', () => {
      state.result = r.plan;
      state.steps = r.plan.steps;
      renderResult();
      $('result-view').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    line.appendChild(button);
    box.appendChild(line);
  } else if (r.planError) {
    box.appendChild(el('p', 'qubo-bad', 'No move plan: ' + r.planError));
  }
}

async function loadQuboFiles() {
  try {
    const res = await fetch('/api/qubo-files');
    const data = await res.json();
    const select = $('qubo-file');
    select.textContent = '';
    (data.files || []).forEach((f) => {
      const option = el('option', null,
        `${f.name}  (${(f.size / 1024).toFixed(1)} kB)`);
      option.value = f.name;
      select.appendChild(option);
    });
    if (!select.options.length) {
      const option = el('option', null, 'no .qubo file — run with "Also export QUBO"');
      option.value = '';
      select.appendChild(option);
      $('qubo-load').disabled = true;
    } else {
      $('qubo-load').disabled = false;
    }
  } catch (err) {
    /* the sidebar section just stays empty */
  }
}

$('qubo-load').addEventListener('click', loadQubo);
$('qubo-search').addEventListener('click', () => evaluateQubo({ search: true }));
$('qubo-cheapest').addEventListener('click', () => evaluateQubo({ cheapest: true }));
$('qubo-eval').addEventListener('click', () => evaluateQubo());
$('qubo-selection').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') evaluateQubo();
});
$('qubo-canvas').addEventListener('mousemove', quboHover);
$('qubo-canvas').addEventListener('mouseleave', () => $('qubo-tip').classList.add('hidden'));
window.addEventListener('resize', () => { if (qubo.model) drawQuboMatrix(); });
$('theme-select').addEventListener('change', () => {
  if (qubo.model) { drawQuboMatrix(); renderQuboLegend(); }
});

loadQuboFiles();
