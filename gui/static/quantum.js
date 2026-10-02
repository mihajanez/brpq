/* Quantum tab: the loaded QUBO on a D-Wave annealer (embedding, classical
   samplers, Leap) and as a QAOA circuit (Qiskit build, estimates, Aer
   simulation, IBM Quantum jobs), plus a comparison of every run against the
   classical IP optimum. Uses the helpers and state of app.js / qubo.js and the
   charts of charts.js. */
'use strict';

const Q = {
  status: null,
  path: null,           // QUBO the quantum state below belongs to
  sub: 'qubo',
  embedding: null,
  zoom: 'fit',
  hoverVar: null,
  anneal: null,
  build: null,
  diagramKind: 'logical',
  estimate: null,
  sim: null,
  landscape: null,
  ibmResult: null,
  ibmConnected: false,
  runs: [],
  activeJobs: {},
};

/* ------------------------------------------------------------------ helpers */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

async function qapi(action, payload) {
  const url = '/api/quantum/' + action;
  const res = payload === undefined
    ? await fetch(url)
    : await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                         body: JSON.stringify(payload) });
  let data;
  try { data = await res.json(); } catch (err) { throw new Error(`${action}: HTTP ${res.status}`); }
  if (data && data.error) throw new Error(data.error);
  return data;
}

function qError(message) {
  const box = $('q-error');
  box.textContent = '';
  box.appendChild(el('strong', null, message));
  const close = el('button', 'btn btn-ghost btn-small', 'Dismiss');
  close.type = 'button';
  close.addEventListener('click', () => box.classList.add('hidden'));
  box.appendChild(close);
  box.classList.remove('hidden');
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function busy(id, on, note) {
  $(id).classList.toggle('hidden', !on);
  if (note !== undefined) {
    const span = $(id).querySelector('span:not(.spinner)');
    if (span) span.textContent = note;
  }
}

function quboPayload(extra) {
  return {
    path: qubo.model.path,
    instance: $('instance-select').value,
    emptyTiers: numberOrNull($('p-empty-tiers').value),
    maximumHeight: numberOrNull($('p-max-height').value),
    ...(extra || {}),
  };
}

function val(id) { return numberOrNull($(id).value); }

/* Start a background job on the server and poll it until it ends. */
async function runJob(action, payload, hooks) {
  const started = await qapi(action, payload);
  const id = started.job;
  Q.activeJobs[action] = id;
  let since = 0;
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      const view = await qapi(`job?id=${id}&since=${since}`);
      since += view.points.length;
      if (hooks && hooks.onPoints && view.points.length) hooks.onPoints(view.points, view);
      if (hooks && hooks.onNote && view.notes.length) hooks.onNote(view.notes[view.notes.length - 1].text, view);
      if (view.status === 'running') continue;
      if (view.status === 'done') return { ...view.result, _context: started };
      if (view.status === 'cancelled') throw new Error('stopped');
      throw new Error(view.error || `${action} failed`);
    }
  } finally {
    delete Q.activeJobs[action];
  }
}

function cancelJob(action) {
  const id = Q.activeJobs[action];
  if (id) qapi('cancel', { id }).catch(() => {});
}

function objectiveOf(energy) {
  return energy === null || energy === undefined ? null : energy + (qubo.model.offset || 0);
}

function ipObjective() {
  const meta = qubo.model && qubo.model.meta;
  if (meta && meta.ipObjective !== undefined && meta.ipObjective !== null) return meta.ipObjective;
  const r = state.classicalResult;
  if (r && meta && r.instance && meta.name === r.instance.name) return r.objective;
  if (r && !meta && r.instance && r.instance.name === $('instance-select').value) return r.objective;
  return null;
}

function contextNote(ctx) {
  if (!ctx || !ctx._context) return '';
  const c = ctx._context;
  if (!c.instance) return 'no test case to replay against';
  return c.contextSource === 'export'
    ? `replayed against ${c.instance} (height ${c.heightLimit}), the case this QUBO was exported from`
    : `replayed against ${c.instance} (height ${c.heightLimit}) from the sidebar — this QUBO has no export record`;
}

/* ----------------------------------------------------------------- views */
function setView(view) {
  document.querySelectorAll('.main-tab').forEach((t) => {
    const on = t.dataset.view === view;
    t.classList.toggle('is-active', on);
    t.setAttribute('aria-selected', String(on));
  });
  $('view-classical').classList.toggle('hidden', view !== 'classical');
  $('view-quantum').classList.toggle('hidden', view !== 'quantum');
  if (view === 'quantum') {
    renderPipeline();
    if (Q.sub === 'anneal' && Q.embedding) drawEmbedding();
    if (Q.sub === 'summary') renderSummary();
  } else {
    renderPreview();
    renderCurrent();
  }
}

function setSub(sub) {
  Q.sub = sub;
  document.querySelectorAll('#quantum-subnav .seg-btn').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.sub === sub));
  ['qubo', 'anneal', 'qaoa', 'summary'].forEach((name) =>
    $('q-' + name).classList.toggle('hidden', name !== sub));
  if (sub === 'qubo' && qubo.model) drawQuboMatrix();
  if (sub === 'anneal') { updateTiming(); if (Q.embedding) drawEmbedding(); }
  if (sub === 'qaoa' && qubo.model && !Q.build) buildCircuit();
  if (sub === 'summary') renderSummary();
}

function renderToolkit() {
  const host = $('quantum-toolkit');
  host.textContent = '';
  const s = Q.status;
  if (!s) return;
  if (!s.available) {
    host.appendChild(el('p', 'qubo-bad',
      `Quantum tools unavailable in this Python (${s.python}): ${s.error}. ` +
      'Start the GUI with the project venv: .venv/bin/python -m gui.server'));
    return;
  }
  host.appendChild(el('h3', 'subhead', 'Toolkits found'));
  const list = el('dl', 'kv');
  Object.entries(s.libraries).forEach(([name, version]) => {
    list.appendChild(el('dt', null, name));
    list.appendChild(el('dd', version ? 'mono' : 'mono qubo-bad', version || 'missing'));
  });
  host.appendChild(list);
}

function renderPipeline() {
  const host = $('pipeline');
  if (!host) return;
  host.textContent = '';
  const meta = qubo.model && qubo.model.meta;
  const ip = ipObjective();
  const best = (kind) => {
    const runs = Q.runs.filter((r) => r.kind === kind && r.objective !== null);
    return runs.length ? Math.min(...runs.map((r) => r.objective)) : null;
  };
  const anneal = best('anneal');
  const qaoa = best('qaoa');
  const hardware = Q.runs.filter((r) => r.kind === 'ibm' || r.hardware);
  const steps = [
    { title: 'Test case', value: meta ? meta.name : ($('instance-select').value || '—'),
      done: true, go: () => setView('classical') },
    { title: 'Classical IP', value: ip !== null ? `${ip} relocations` : 'not run',
      done: ip !== null, go: () => setView('classical') },
    { title: 'QUBO', value: qubo.model ? `${qubo.model.stats.columns} variables` : '—',
      done: !!qubo.model, go: () => setSub('qubo') },
    { title: 'Annealing', value: anneal !== null ? `best ${fmt(anneal)}` : 'not run',
      done: anneal !== null, go: () => setSub('anneal') },
    { title: 'QAOA simulation', value: qaoa !== null ? `best ${fmt(qaoa)}` : 'not run',
      done: qaoa !== null, go: () => setSub('qaoa') },
    { title: 'Quantum hardware', value: hardware.length ? `${hardware.length} run(s)` : 'optional',
      done: hardware.length > 0, go: () => setSub('qaoa') },
    { title: 'Comparison', value: `${Q.runs.length} run(s)`, done: Q.runs.length > 0,
      go: () => setSub('summary') },
  ];
  steps.forEach((step, i) => {
    const li = el('li', 'pipeline-step' + (step.done ? ' is-done' : ''));
    const button = el('button', null);
    button.type = 'button';
    button.appendChild(el('span', 'pipeline-num', step.done ? '✓' : String(i + 1)));
    const text = el('span', 'pipeline-text');
    text.appendChild(el('span', 'pipeline-title', step.title));
    text.appendChild(el('span', 'pipeline-value', step.value));
    button.appendChild(text);
    button.addEventListener('click', () => { setView('quantum'); step.go(); });
    li.appendChild(button);
    host.appendChild(li);
  });
  $('quantum-badge').textContent = qubo.model
    ? `${qubo.model.stats.columns} vars` + (Q.runs.length ? ` · ${Q.runs.length} runs` : '')
    : 'no QUBO';
}

/* Called by qubo.js whenever a QUBO is loaded. */
function onQuboLoaded(model) {
  if (Q.path !== model.path || Q.modified !== model.stats.columns + ':' + (model.meta && model.meta.created)) {
    Q.path = model.path;
    Q.modified = model.stats.columns + ':' + (model.meta && model.meta.created);
    Q.embedding = null; Q.anneal = null; Q.build = null; Q.estimate = null;
    Q.sim = null; Q.landscape = null; Q.ibmResult = null; Q.runs = [];
    ['a-embed-result', 'a-result', 'c-result', 'e-result', 's-result', 'i-result',
     's-trace-panel', 's-landscape-panel'].forEach((id) => $(id).classList.add('hidden'));
    $('i-check-result').textContent = '';
    $('s-init').value = '';
  }
  $('quantum-empty').classList.add('hidden');
  $('quantum-main').classList.remove('hidden');
  const exact = model.stats.columns <= ((Q.status && Q.status.exactLimit) || 24);
  $('s-run').disabled = !exact;
  $('s-landscape').disabled = !exact;
  $('s-run').title = exact ? '' : `statevector simulation stops at ${Q.status.exactLimit} qubits`;
  renderPipeline();
  refreshNeedsAngles();
  renderIbmJobs();
  if (Q.sub === 'qaoa') buildCircuit();
  if (Q.sub === 'anneal') updateTiming();
}

/* ============================================================ annealing */
const METHOD_HELP = {
  'sa': 'Classical Metropolis simulation of annealing on the logical QUBO: the reference ' +
        'for what an ideal annealer with all-to-all couplers would return.',
  'sa-embedded': 'Anneals the physical problem the QPU would actually see — chains of qubits held ' +
        'by the chain strength — then reads each chain back by majority vote. Shows what the embedding costs.',
  'tabu': 'Multistart tabu search (dwave-samplers): a strong classical heuristic, usually the ' +
          'quickest way to the optimum on small QUBOs.',
  'steepest': 'Greedy single-flip descent from random starts: a cheap lower bar any annealer should beat.',
  'tree': 'Exact solver by tree decomposition: optimal whenever the interaction graph has small treewidth.',
  'exact': 'Enumerates all 2^n assignments (n ≤ 24) and returns the lowest — the ground truth.',
  'random': 'Uniformly random bitstrings: the baseline that shows how rare good selections are.',
  'qpu': 'Real D-Wave QPU through Leap: the QUBO is embedded on the live chip (its own yield), ' +
         'annealed num_reads times. Uses QPU time on your Leap account.',
  'hybrid': 'Leap hybrid BQM solver: D-Wave\'s classical–quantum hybrid service. Uses Leap time.',
};

function annealMethodChanged() {
  const method = $('a-method').value;
  document.querySelectorAll('#q-anneal [data-for]').forEach((node) => {
    node.classList.toggle('hidden', !node.dataset.for.split(' ').includes(method));
  });
  $('a-method-help').textContent = METHOD_HELP[method] || '';
  const cloud = method === 'qpu' || method === 'hybrid';
  $('a-run').textContent = cloud ? 'Submit to D-Wave Leap' : 'Run sampler';
  $('a-run').classList.toggle('btn-danger', false);
  updateTiming();
}

async function embedProblem() {
  if (!qubo.model) return;
  busy('a-embed-busy', true);
  $('a-embed').disabled = true;
  try {
    const r = await qapi('embed', quboPayload({
      topology: $('a-topology').value, seed: val('a-embed-seed') ?? 1,
      tries: val('a-embed-tries') ?? 10,
    }));
    Q.embedding = r;
    renderEmbedding();
    updateTiming();
  } catch (err) {
    qError('Embedding failed: ' + err.message);
  } finally {
    busy('a-embed-busy', false);
    $('a-embed').disabled = false;
  }
}

function renderEmbedding() {
  const e = Q.embedding;
  $('a-embed-result').classList.remove('hidden');
  const row = $('a-embed-stats');
  row.textContent = '';
  row.appendChild(statTile('Physical qubits', String(e.physical),
    `for ${e.logical} logical variables`, true));
  row.appendChild(statTile('Longest chain', String(e.maxChain),
    `mean ${e.meanChain.toFixed(2)} qubits`));
  row.appendChild(statTile('Chip used', pct(e.chipFraction), `of ${e.targetQubits.toLocaleString()} qubits`));
  row.appendChild(statTile('Couplers', String(e.couplersUsed),
    `${e.logicalCouplers} logical + chain links`));
  row.appendChild(statTile('Chain strength', fmt(e.chainStrength),
    `auto (torque comp.) · max bias ${fmt(e.maxBias)}`));
  row.appendChild(statTile('Embedding time', `${e.seconds.toFixed(2)}s`, `seed ${e.seed}`));

  const legend = $('a-embed-legend');
  legend.textContent = '';
  [['var(--series-1)', 'chain (one variable)', false], ['var(--series-2)', 'logical coupler used', true],
   ['var(--chip-dot)', 'unused qubit', false]].forEach(([color, text, line]) => {
    const key = el('span', 'legend-key');
    const sw = el('span', line ? 'swatch-line' : 'swatch');
    sw.style.background = color;
    key.append(sw, text);
    legend.appendChild(key);
  });
  legend.appendChild(el('span', 'hint', 'hover a qubit to trace its chain'));

  const hist = e.chainHistogram.map(([len, count]) => `${count} × length ${len}`).join(', ');
  const notes = $('a-embed-notes');
  notes.innerHTML = '';
  const p = el('p');
  p.innerHTML = `minorminer placed the ${e.logical} variables on <strong>${e.physical}</strong> physical ` +
    `qubits of the ${escapeHtml(e.label)} graph (chains: ${hist}). The QPU timing model below now uses ` +
    `${e.physical} qubits. This is the ideal, full-yield graph; a live QPU has a few inactive qubits, ` +
    'so a real submission re-embeds against the chip it lands on.';
  notes.appendChild(p);
  if (e.maxChain > 6) {
    notes.appendChild(el('p', 'qubo-bad',
      'Chains longer than ~6 break easily; expect a raised chain-break fraction on hardware.'));
  }
  $('a-embed-download').href = `/api/quantum/export?what=embedding-json&path=` +
    `${encodeURIComponent(qubo.model.path)}&embedding=${e.id}`;
  drawEmbedding();
}

function drawEmbedding() {
  const e = Q.embedding;
  if (!e) return;
  const canvas = $('a-embed-canvas');
  const host = canvas.parentElement;
  const width = Math.max(300, Math.min(host.clientWidth || 700, 900));
  const height = Math.round(width * 0.62);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = cssVar('--surface-1');
  ctx.fillRect(0, 0, width, height);

  const used = new Set(e.chains.flat());
  let [x0, y0, x1, y1] = [0, 0, 1, 1];
  if (Q.zoom === 'fit') {
    const xs = [...used].map((i) => e.xy[i][0]), ys = [...used].map((i) => e.xy[i][1]);
    x0 = Math.min(...xs); x1 = Math.max(...xs); y0 = Math.min(...ys); y1 = Math.max(...ys);
    const padX = Math.max(0.03, (x1 - x0) * 0.15), padY = Math.max(0.03, (y1 - y0) * 0.15);
    x0 -= padX; x1 += padX; y0 -= padY; y1 += padY;
  }
  const scale = Math.min((width - 20) / (x1 - x0), (height - 20) / (y1 - y0));
  const ox = (width - scale * (x1 - x0)) / 2, oy = (height - scale * (y1 - y0)) / 2;
  const px = (i) => [ox + (e.xy[i][0] - x0) * scale, oy + (e.xy[i][1] - y0) * scale];
  Q.embedProject = { px, used };

  // every qubit in view, faintly
  ctx.fillStyle = cssVar('--chip-dot');
  const dot = Q.zoom === 'fit' ? 2.2 : 1.1;
  for (let i = 0; i < e.xy.length; i++) {
    const [x, y] = px(i);
    if (x < -2 || y < -2 || x > width + 2 || y > height + 2) continue;
    ctx.fillRect(x - dot / 2, y - dot / 2, dot, dot);
  }
  const series1 = cssVar('--series-1'), series2 = cssVar('--series-2');
  const target = cssVar('--target'), surface = cssVar('--surface-1');
  // logical couplers between chains
  ctx.lineWidth = 1;
  ctx.strokeStyle = series2;
  e.couplers.forEach(([a, b, i, j]) => {
    const hot = Q.hoverVar !== null && (i === Q.hoverVar || j === Q.hoverVar);
    ctx.globalAlpha = Q.hoverVar === null || hot ? 0.9 : 0.25;
    ctx.lineWidth = hot ? 2 : 1;
    const [ax, ay] = px(a), [bx, by] = px(b);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  });
  ctx.globalAlpha = 1;
  // chain links
  e.chainEdges.forEach(([a, b, v]) => {
    ctx.strokeStyle = v === Q.hoverVar ? target : series1;
    ctx.lineWidth = Q.zoom === 'fit' ? 3 : 2;
    const [ax, ay] = px(a), [bx, by] = px(b);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  });
  // chain qubits with a surface ring
  const r = Q.zoom === 'fit' ? 4.5 : 2.5;
  e.chains.forEach((chain, v) => {
    chain.forEach((i) => {
      const [x, y] = px(i);
      ctx.beginPath(); ctx.arc(x, y, r + 1.5, 0, Math.PI * 2);
      ctx.fillStyle = surface; ctx.fill();
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = v === Q.hoverVar ? target : series1; ctx.fill();
    });
  });
}

function embedHover(event) {
  const e = Q.embedding, proj = Q.embedProject;
  if (!e || !proj) return;
  const rect = $('a-embed-canvas').getBoundingClientRect();
  const mx = event.clientX - rect.left, my = event.clientY - rect.top;
  let best = null, bestD = 12 * 12;
  e.chains.forEach((chain, v) => chain.forEach((i) => {
    const [x, y] = proj.px(i);
    const d = (x - mx) ** 2 + (y - my) ** 2;
    if (d < bestD) { bestD = d; best = { v, i }; }
  }));
  const tip = $('a-embed-tip');
  const previous = Q.hoverVar;
  Q.hoverVar = best ? best.v : null;
  if (previous !== Q.hoverVar) drawEmbedding();
  if (!best) { tip.classList.add('hidden'); return; }
  const neighbours = e.couplers.filter(([, , i, j]) => i === best.v || j === best.v).length;
  tip.textContent = `qubit ${best.i} · ${e.names[best.v]} · chain of ${e.chains[best.v].length} · ` +
    `${neighbours} logical coupler(s)`;
  tip.classList.remove('hidden');
  const host = $('a-embed-canvas').parentElement.getBoundingClientRect();
  tip.style.left = Math.min(event.clientX - host.left + 12, host.width - 260) + 'px';
  tip.style.top = (event.clientY - host.top + 12) + 'px';
}

let timingTimer = null;
function updateTiming() {
  clearTimeout(timingTimer);
  timingTimer = setTimeout(async () => {
    if (!qubo.model || !Q.status || !Q.status.available) return;
    const qubits = Q.embedding ? Q.embedding.physical : qubo.model.stats.columns;
    const reads = val('a-reads') || 1000;
    try {
      const t = await qapi('qpu-timing', { topology: Q.embedding ? Q.embedding.topology : $('a-topology').value,
                                           qubits, reads, annealTime: val('a-anneal-time') || 20 });
      const box = $('a-timing');
      box.textContent = '';
      box.appendChild(el('strong', null,
        `Estimated QPU access time: ${(t.totalUs / 1000).toFixed(1)} ms`));
      box.appendChild(el('span', null,
        ` for ${reads.toLocaleString()} reads on ${qubits} qubits` +
        (Q.embedding ? ' (this embedding)' : ' (one per variable — find an embedding for the real count)') +
        ` = programming ${(t.programmingUs / 1000).toFixed(1)} ms + ${reads.toLocaleString()} × ` +
        `(anneal ${fmt(t.annealUs)} µs + readout & delay ${fmt(t.readoutAndDelayUs)} µs). ` +
        `${t.model}; excludes network and queue time.`));
    } catch (err) {
      $('a-timing').textContent = '';
    }
  }, 250);
}

async function runAnneal() {
  if (!qubo.model) return;
  const method = $('a-method').value;
  if (method === 'sa-embedded' && !Q.embedding) {
    qError('Find an embedding first (step 1): this method anneals the embedded problem.');
    return;
  }
  if ((method === 'qpu' || method === 'hybrid') &&
      !window.confirm(`Submit this QUBO to D-Wave Leap (${method === 'qpu' ? 'QPU' : 'hybrid solver'})? ` +
                      'It uses solver time on your Leap account.')) {
    return;
  }
  const payload = quboPayload({
    method, reads: val('a-reads'), sweeps: val('a-sweeps'), seed: val('a-seed'),
    annealTime: val('a-anneal-time'), chainStrength: val('a-chain'),
    tabuTimeout: val('a-tabu-timeout'), topology: Q.embedding ? Q.embedding.topology : $('a-topology').value,
    embeddingId: Q.embedding ? Q.embedding.id : null,
    token: $('a-token').value.trim() || null, solver: $('a-solver').value.trim() || null,
    timeLimit: val('a-time-limit'),
  });
  busy('a-run-busy', true, method === 'qpu' || method === 'hybrid' ? 'waiting for Leap…' : 'sampling…');
  $('a-run').disabled = true;
  try {
    const r = await runJob('anneal', payload);
    Q.anneal = r;
    renderAnneal(r);
    recordRun({
      kind: 'anneal', hardware: method === 'qpu' || method === 'hybrid',
      label: r.methodLabel, platform: method === 'qpu' ? (r.solver || 'D-Wave QPU')
        : method === 'hybrid' ? 'Leap hybrid' : 'classical (this machine)',
      scores: r.scores, seconds: r.wallTime,
      note: `${r.reads} reads` + (r.chainBreakFraction !== undefined ? `, chain breaks ${pct(r.chainBreakFraction)}` : ''),
      qpu: r.timing && r.timing.qpu_access_time ? `${(r.timing.qpu_access_time / 1000).toFixed(1)} ms (measured)`
        : `${(r.qpuEstimate.totalUs / 1000).toFixed(1)} ms (est.)`,
    });
  } catch (err) {
    qError('Sampling failed: ' + err.message);
  } finally {
    busy('a-run-busy', false);
    $('a-run').disabled = false;
  }
}

function scoreTiles(row, s, extra) {
  const ip = ipObjective();
  const bestPlan = s.plan ? s.plan.objective : null;
  row.appendChild(statTile('Best objective', fmt(s.bestObjective),
    `energy ${fmt(s.bestEnergy)} + offset ${fmt(s.offset)}` +
    (ip !== null ? (s.bestObjective <= ip + 1e-9 ? ' · matches the IP optimum' : ` · IP optimum ${ip}`) : ''), true));
  row.appendChild(statTile('Best plan', bestPlan !== null ? `${bestPlan} moves` : '—',
    s.best ? (s.best.filled ? `${s.best.filled} move(s) completed greedily` : 'replays as encoded')
           : 'no feasible sample replays'));
  if (s.optimumFraction !== undefined) {
    const uni = s.optimum && s.optimum.uniform && s.optimum.uniform.optimumFraction;
    row.appendChild(statTile('Hit the optimum', pct(s.optimumFraction),
      `${s.optimumHits.toLocaleString()} of ${s.shots.toLocaleString()}` +
      (uni ? ` · random ${pct(uni)}` : '')));
  }
  row.appendChild(statTile('Feasible', pct(s.feasibleFraction),
    'one sequence per blocking block' + (s.optimum && s.optimum.uniform
      ? ` · random ${pct(s.optimum.uniform.feasibleFraction)}` : '')));
  row.appendChild(statTile('Mean energy', fmt(s.meanEnergy),
    s.optimum && s.optimum.uniform ? `random guessing ${fmt(s.optimum.uniform.meanEnergy)}` : ''));
  (extra || []).forEach((t) => row.appendChild(t));
}

function renderAnneal(r) {
  $('a-result').classList.remove('hidden');
  const s = r.scores;
  const row = $('a-stats');
  row.textContent = '';
  const extra = [statTile('Wall time', `${fmt(r.wallTime)}s`,
    `${(1000 * r.tts.secondsPerRead).toPrecision(3)} ms per read here`)];
  if (r.tts.seconds !== null && r.tts.seconds !== undefined) {
    extra.push(statTile('Time to solution', `${fmt(r.tts.seconds * 1000)} ms`,
      `99% confidence · ${r.tts.reads} read(s) here`));
  }
  if (r.tts.qpuSeconds) {
    extra.push(statTile('Est. QPU time to solution', `${fmt(r.tts.qpuSeconds * 1000)} ms`,
      `${r.tts.qpuReads} QPU read(s), at this run's hit rate`));
  }
  if (r.chainBreakFraction !== undefined) {
    extra.push(statTile('Broken chains', pct(r.chainBreakFraction),
      `${pct(r.readsWithBrokenChains || 0)} of reads had one`));
  }
  extra.push(statTile(r.timing && r.timing.qpu_access_time ? 'QPU access time' : 'Est. QPU access time',
    r.timing && r.timing.qpu_access_time ? `${(r.timing.qpu_access_time / 1000).toFixed(1)} ms`
      : `${(r.qpuEstimate.totalUs / 1000).toFixed(1)} ms`,
    `${r.reads} reads × ${fmt(r.qpuEstimate.annealUs)} µs on ${r.qpuEstimate.qubits} qubits`));
  scoreTiles(row, s, extra);
  energyHistogram($('a-hist'), $('a-hist-legend'), s);
  sampleTable($('a-samples'), s.samples, r.methodLabel);
  annealInterpretation(r);
}

function energyHistogram(host, legend, s) {
  const optimum = s.optimum && s.optimum.energy;
  const data = s.histogram.map((h) => ({
    label: h.rest ? `≥${fmt(objectiveOf(h.energy))}` : fmt(objectiveOf(h.energy)),
    values: [h.feasible, h.count - h.feasible],
    mark: optimum !== null && optimum !== undefined && Math.abs(h.energy - optimum) < 1e-6 ? 'optimum' : null,
    tip: `<strong>objective ${fmt(objectiveOf(h.energy))}</strong> (energy ${fmt(h.energy)})<br>` +
         `${h.count.toLocaleString()} reads · ${h.feasible.toLocaleString()} feasible`,
  }));
  columnChart(host, data, {
    series: [{ name: 'feasible (one sequence per block)', color: 'var(--series-1)' },
             { name: 'infeasible', color: 'var(--series-2)' }],
    stacked: true, legend, xLabel: 'objective = energy + offset (lower is better)',
    label: 'histogram of sample energies',
  });
}

function sampleTable(table, samples, label, opts) {
  table.textContent = '';
  const head = el('thead');
  const hr = el('tr');
  const prob = opts && opts.probability;
  ['Objective', 'Energy', prob ? 'Probability' : 'Count', 'Feasible', 'Plan', 'Columns set',
   'Bitstring'].forEach((h) => hr.appendChild(el('th', null, h)));
  head.appendChild(hr);
  table.appendChild(head);
  const body = el('tbody');
  samples.forEach((s) => {
    const tr = el('tr');
    tr.appendChild(el('td', null, fmt(s.objective)));
    tr.appendChild(el('td', null, fmt(s.energy)));
    tr.appendChild(el('td', null, prob ? pct(s.probability) : `${s.count.toLocaleString()} (${pct(s.probability)})`));
    tr.appendChild(el('td', s.feasible ? 'qubo-ok' : 'qubo-bad', s.feasible ? '✓ yes' : '✗ ' + s.violations.length + ' group(s)'));
    tr.appendChild(el('td', null, s.relocations !== null
      ? `${s.relocations} moves${s.filled ? ` (${s.filled} filled)` : ''}` : (s.feasible ? 'does not replay' : '—')));
    tr.appendChild(el('td', 'mono', s.names.filter((n) => n.startsWith('x(')).join(' ')));
    tr.appendChild(el('td', 'mono bits', s.bitstring));
    if (s.relocations !== null) {
      tr.title = 'Play this selection as a move plan';
      tr.addEventListener('click', () => playBitstring(s.bitstring, label));
    } else {
      tr.classList.add('is-static');
      tr.title = s.planError || 'infeasible: no plan';
    }
    body.appendChild(tr);
  });
  table.appendChild(body);
}

async function playBitstring(bitstring, label) {
  try {
    const res = await fetch('/api/qubo-eval', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(quboPayload({ bitstring, complete: true, label })),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (!data.plan) throw new Error(data.planError || 'this selection does not replay');
    showPlan(data.plan, label);
  } catch (err) {
    qError('Cannot play this sample: ' + err.message);
  }
}

function annealInterpretation(r) {
  const s = r.scores, box = $('a-interpretation');
  box.textContent = '';
  const ip = ipObjective();
  const opt = s.optimum || {};
  const p = el('p');
  const reached = opt.energy !== null && opt.energy !== undefined && s.bestEnergy <= opt.energy + 1e-9;
  p.innerHTML = `<strong>${escapeHtml(r.methodLabel)}</strong> returned ${s.shots.toLocaleString()} reads ` +
    `(${s.distinct.toLocaleString()} distinct). The best has energy <strong>${fmt(s.bestEnergy)}</strong>, i.e. ` +
    `objective ${fmt(s.bestObjective)} = relocations if feasible` +
    (reached ? ' — the QUBO\'s ground state' : opt.energy !== null && opt.energy !== undefined
      ? `, ${fmt(s.bestEnergy - opt.energy)} above the ground state (${fmt(opt.objective)})` : '') + '.';
  box.appendChild(p);
  const list = el('ul');
  if (ip !== null) {
    list.appendChild(el('li', null, s.bestObjective <= ip + 1e-9
      ? `It matches the classical IP optimum of ${ip} relocations: the quantum formulation and the exact model agree.`
      : `The classical IP optimum is ${ip}; this run stopped ${fmt(s.bestObjective - ip)} short.`));
  }
  if (s.optimumFraction !== undefined) {
    const uni = opt.uniform && opt.uniform.optimumFraction;
    list.appendChild(el('li', null,
      `${pct(s.optimumFraction)} of reads hit the optimum` + (uni
        ? ` — random guessing would hit it with probability ${pct(uni)}, so the sampler concentrates ` +
          `on it ${uni > 0 ? fmt(s.optimumFraction / uni) + '×' : ''} more often.` : '.')));
  }
  list.appendChild(el('li', null,
    `${pct(s.feasibleFraction)} of reads are feasible (exactly one sequence per blocking block); ` +
    'the rest violate a one-hot penalty and do not encode a plan.'));
  if (r.chainBreakFraction !== undefined) {
    list.appendChild(el('li', null,
      `On the embedded problem ${pct(r.chainBreakFraction)} of chain qubits disagreed and were repaired by ` +
      'majority vote. Raise the chain strength if this is high; too strong a chain flattens the problem instead.'));
  }
  if (r.tts && r.tts.seconds) {
    list.appendChild(el('li', null,
      `Time to solution (99% confidence): ${fmt(r.tts.seconds * 1000)} ms on this machine` +
      (r.tts.qpuSeconds ? `; a QPU with the same per-read success rate would need about ` +
        `${fmt(r.tts.qpuSeconds * 1000)} ms of access time, mostly the one-off programming time.` : '.')));
  }
  if (s.best && s.best.filled) {
    list.appendChild(el('li', null,
      `The best sample selects ${s.best.filled === 1 ? 'a provisional sequence' : 'provisional sequences'} ` +
      `whose final destination the QUBO leaves open; the plan shown completes ${s.best.filled} move(s) ` +
      'greedily, so it can need more relocations than the energy says.'));
  }
  box.appendChild(list);
  if (s.plan) {
    const button = el('button', 'btn btn-primary', `Play the best plan (${s.plan.objective} moves)`);
    button.type = 'button';
    button.addEventListener('click', () => showPlan(s.plan, r.methodLabel));
    box.appendChild(button);
  }
  const note = contextNote(r);
  if (note) box.appendChild(el('p', 'help', note));
}

/* ================================================================= QAOA */
async function buildCircuit() {
  if (!qubo.model || !Q.status || !Q.status.qaoa) return;
  busy('c-build-busy', true);
  $('c-build').disabled = true;
  try {
    const reps = val('c-reps') || 1;
    Q.build = await qapi('qaoa-build', quboPayload({ reps }));
    renderBuild();
  } catch (err) {
    qError('Could not build the circuit: ' + err.message);
  } finally {
    busy('c-build-busy', false);
    $('c-build').disabled = false;
  }
}

function renderBuild() {
  const b = Q.build;
  $('c-result').classList.remove('hidden');
  const row = $('c-stats');
  row.textContent = '';
  row.appendChild(statTile('Qubits', String(b.qubits), 'one per QUBO column', true));
  row.appendChild(statTile('Angles', String(b.parameters.length), b.parameters.join(', ')));
  row.appendChild(statTile('Pauli terms', String(b.linearTerms + b.quadraticTerms),
    `${b.linearTerms} Z + ${b.quadraticTerms} ZZ`));
  row.appendChild(statTile('Logical depth', String(b.depth),
    Object.entries(b.ops).map(([g, c]) => `${c} ${g}`).join(' · ')));
  row.appendChild(statTile('In basis gates', `${b.basis.twoQubit} CX`,
    `depth ${b.basis.depth} · before routing`));
  row.appendChild(statTile('Statevector', b.statevectorBytes ? humanBytes(b.statevectorBytes) : '—',
    b.exact ? 'simulable here' : `beyond the ${Q.status.exactLimit}-qubit limit`));

  const table = $('c-hamiltonian');
  table.textContent = '';
  const head = el('tr');
  ['Term', 'Coefficient', 'Columns'].forEach((h) => head.appendChild(el('th', null, h)));
  const thead = el('thead'); thead.appendChild(head); table.appendChild(thead);
  const body = el('tbody');
  b.hamiltonian.forEach((t) => {
    const tr = el('tr', 'is-static');
    tr.appendChild(el('td', 'mono', t.qubits.map((q) => `Z${q}`).join(' ')));
    tr.appendChild(el('td', null, fmt(t.coeff, 4)));
    tr.appendChild(el('td', 'mono', t.qubits.map((q) => b.names[q]).join(' · ')));
    body.appendChild(tr);
  });
  table.appendChild(body);
  $('c-ham-count').textContent = String(b.hamiltonian.length);
  setDiagram(Q.diagramKind);
}

function humanBytes(n) {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}

function setDiagram(kind) {
  Q.diagramKind = kind;
  document.querySelectorAll('#c-diagram-kind .seg-btn').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.kind === kind));
  $('c-diagram-device').classList.toggle('hidden', kind !== 'transpiled');
  if (!Q.build) return;
  const url = `/api/quantum/diagram?path=${encodeURIComponent(qubo.model.path)}&reps=${Q.build.reps}` +
    `&kind=${kind}&device=${encodeURIComponent($('c-diagram-device').value)}&fold=${kind === 'logical' ? 30 : 40}`;
  const img = $('c-diagram');
  $('c-diagram-error').classList.add('hidden');
  $('c-diagram-open').href = url;
  busy('c-diagram-busy', true);
  img.classList.add('is-loading');
  img.onload = () => { busy('c-diagram-busy', false); img.classList.remove('is-loading', 'hidden'); };
  img.onerror = async () => {
    busy('c-diagram-busy', false);
    img.classList.add('hidden');
    let message = 'could not draw the circuit';
    try { const r = await fetch(url); const d = await r.json(); message = d.error || message; } catch (e) { /* keep */ }
    $('c-diagram-error').textContent = message;
    $('c-diagram-error').classList.remove('hidden');
  };
  img.classList.remove('is-zoomed');
  img.src = url;
}

function currentAngles() {
  if (Q.sim && Q.sim.reps === (Q.build && Q.build.reps)) return Q.sim.parameters;
  const typed = $('s-init').value.split(/[\s,]+/).filter(Boolean).map(Number);
  if (Q.build && typed.length === 2 * Q.build.reps && typed.every(Number.isFinite)) return typed;
  return null;
}

function refreshNeedsAngles() {
  const ok = !!currentAngles();
  document.querySelectorAll('.needs-angles').forEach((a) => {
    a.classList.toggle('is-disabled', !ok);
    a.title = ok ? '' : 'needs tuned angles: run the simulation (or type initial angles) first';
  });
  $('i-submit').disabled = !ok;
  $('i-submit').title = ok ? '' : 'run the QAOA simulation first: the job samples the tuned circuit';
}

function exportClick(event) {
  const what = event.currentTarget.dataset.export;
  event.preventDefault();
  if (!Q.build) return;
  const params = currentAngles();
  if (event.currentTarget.classList.contains('needs-angles') && !params) {
    qError('Tune the angles first: run the QAOA simulation (or type initial angles).');
    return;
  }
  const url = `/api/quantum/export?path=${encodeURIComponent(qubo.model.path)}&what=${what}` +
    `&reps=${Q.build.reps}` + (params ? `&params=${params.map((v) => v.toFixed(8)).join(',')}` : '');
  const a = document.createElement('a');
  a.href = url; a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
}

function renderDeviceChoices() {
  const host = $('e-devices');
  host.textContent = '';
  const defaults = new Set(['FakeFez', 'FakeTorino', 'FakeBrisbane']);
  Q.status.devices.forEach((d) => {
    const label = el('label', 'check inline');
    const box = el('input');
    box.type = 'checkbox'; box.value = d.id; box.checked = defaults.has(d.id);
    label.append(box, el('span', null, `${d.name} · ${d.family} · ${d.qubits} q`));
    host.appendChild(label);
  });
  const sel = $('c-diagram-device');
  sel.textContent = '';
  Q.status.devices.forEach((d) => {
    const o = el('option', null, `${d.name} (${d.family}, ${d.qubits} qubits)`);
    o.value = d.id;
    sel.appendChild(o);
  });
  const noise = $('s-noise');
  Q.status.devices.forEach((d) => {
    const o = el('option', null, `Noisy: ${d.name} noise model (${d.family})`);
    o.value = d.id;
    noise.appendChild(o);
  });
}

async function runEstimate() {
  if (!qubo.model) return;
  if (!Q.build) await buildCircuit();
  const devices = [...document.querySelectorAll('#e-devices input:checked')].map((b) => b.value);
  busy('e-busy', true, 'estimating…');
  $('e-run').disabled = true;
  try {
    Q.estimate = await runJob('qaoa-estimate', quboPayload({
      reps: Q.build ? Q.build.reps : val('c-reps'), shots: val('e-shots') || 4096,
      maxiter: val('s-maxiter') || 100, restarts: val('s-restarts') || 1, devices,
    }), { onNote: (text) => { $('e-note').textContent = text; } });
    renderEstimate();
  } catch (err) {
    qError('Estimate failed: ' + err.message);
  } finally {
    busy('e-busy', false);
    $('e-run').disabled = false;
  }
}

function seconds(s) {
  if (s === null || s === undefined) return '—';
  if (s < 1e-3) return `${(s * 1e6).toFixed(1)} µs`;
  if (s < 1) return `${(s * 1e3).toFixed(1)} ms`;
  if (s < 120) return `${s.toFixed(1)} s`;
  if (s < 7200) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

function renderEstimate() {
  const e = Q.estimate;
  $('e-result').classList.remove('hidden');
  const table = $('e-table');
  table.textContent = '';
  const thead = el('thead'), hr = el('tr');
  ['Target', 'Qubits used', 'Depth', '2-qubit gates', 'Circuit time', 'Est. success prob.',
   `1 sampling job (${e.shots} shots)`, `Optimising on hardware (${e.evaluations} evals)`]
    .forEach((h) => hr.appendChild(el('th', null, h)));
  thead.appendChild(hr); table.appendChild(thead);
  const body = el('tbody');
  e.devices.forEach((d) => {
    const tr = el('tr', 'is-static');
    tr.appendChild(el('td', null, `${d.device}${d.family ? ' · ' + (d.family.family || '') + ' ' + (d.family.revision || '') : ''}`));
    if (!d.fits) {
      const td = el('td', 'qubo-bad', `does not fit: ${d.deviceQubits} qubits`);
      td.colSpan = 7;
      tr.appendChild(td);
    } else {
      tr.appendChild(el('td', null, `${d.physicalQubits} of ${d.deviceQubits}`));
      tr.appendChild(el('td', null, String(d.depth)));
      tr.appendChild(el('td', null, String(d.twoQubit)));
      tr.appendChild(el('td', null, seconds(d.durationS)));
      tr.appendChild(el('td', d.esp < 0.05 ? 'qubo-bad' : null, pct(d.esp)));
      tr.appendChild(el('td', null, seconds(d.samplingS)));
      tr.appendChild(el('td', null, `${seconds(d.optimizationS)} · ${d.jobs} jobs`));
    }
    body.appendChild(tr);
  });
  const sim = e.simulation;
  const tr = el('tr', 'is-static row-sim');
  tr.appendChild(el('td', null, 'Aer statevector (this machine)'));
  tr.appendChild(el('td', null, String(sim.qubits)));
  const memo = el('td', null, `memory ${humanBytes(sim.statevectorBytes)}`);
  memo.colSpan = 3;
  tr.appendChild(memo);
  tr.appendChild(el('td', null, 'exact (no noise)'));
  tr.appendChild(el('td', null, sim.secondsPerEvaluation ? `${seconds(sim.secondsPerEvaluation)} / eval` : '—'));
  tr.appendChild(el('td', null, sim.feasible ? seconds(sim.optimizationS) : `beyond ${Q.status.exactLimit} qubits`));
  body.appendChild(tr);
  table.appendChild(body);

  const box = $('e-interpretation');
  box.textContent = '';
  const fits = e.devices.filter((d) => d.fits);
  const list = el('ul');
  if (fits.length) {
    const best = fits.reduce((a, b) => (b.esp > a.esp ? b : a));
    list.appendChild(el('li', null,
      `Routing onto a heavy-hex / square lattice inflates the circuit: ${best.device} needs ` +
      `${best.twoQubit} two-qubit gates at depth ${best.depth} (logically ${Q.build ? Q.build.basis.twoQubit : '?'} CX before routing).`));
    list.appendChild(el('li', null,
      `Estimated success probability — the chance no gate or readout errs, from the device's calibration — ` +
      `is ${pct(best.esp)} on ${best.device}. ` + (best.esp < 0.05
        ? 'At this level the hardware output is mostly noise: expect samples close to uniform random.'
        : best.esp < 0.3 ? 'A sizeable share of shots will be corrupted; the low-energy signal survives only partly.'
          : 'Enough shots run error-free for the QAOA signal to show.')));
    list.appendChild(el('li', null,
      `One sampling job of ${e.shots} shots costs about ${seconds(best.samplingS)} of QPU time ` +
      `(circuit + ${seconds(best.repDelayS)} reset per shot). Optimising the angles on hardware would take ` +
      `${e.evaluations} such jobs (${seconds(best.optimizationS)}), plus queueing for each — which is why the ` +
      'GUI tunes the angles in simulation and sends only the final sampling job.'));
  }
  list.appendChild(el('li', null, e.simulation.feasible
    ? `On this machine the same optimisation takes about ${seconds(e.simulation.optimizationS)} of statevector ` +
      `simulation (${humanBytes(e.simulation.statevectorBytes)} of amplitudes; doubles with every qubit).`
    : `Simulating ${e.simulation.qubits} qubits exactly would need ${humanBytes(e.simulation.statevectorBytes)} — ` +
      'out of reach classically, so hardware is the only way to run this QAOA.'));
  box.appendChild(list);
}

/* ---------------------------------------------------------- simulation */
function simPayload() {
  const typed = $('s-init').value.split(/[\s,]+/).filter(Boolean).map(Number);
  return quboPayload({
    reps: Q.build ? Q.build.reps : val('c-reps') || 1,
    optimizer: $('s-optimizer').value, maxiter: val('s-maxiter'), restarts: val('s-restarts'),
    shots: val('s-shots'), cvar: val('s-cvar'), seed: val('s-seed'),
    shotNoise: $('s-shot-noise').checked, noiseDevice: $('s-noise').value || null,
    initParams: typed.length ? typed : null,
  });
}

async function runSimulation() {
  if (!qubo.model) return;
  if (!Q.build || Q.build.reps !== (val('c-reps') || 1)) await buildCircuit();
  const payload = simPayload();
  const trace = [];
  $('s-trace-panel').classList.remove('hidden');
  simRunning(true, 'starting…');
  try {
    const r = await runJob('qaoa-run', payload, {
      onPoints: (points) => {
        points.forEach((p) => { if (p.eval) trace.push(p); });
        drawTrace(trace, null);
        const last = trace[trace.length - 1];
        if (last) $('s-note').textContent = `evaluation ${last.eval} · ⟨E⟩ ${fmt(last.energy, 4)} · P(opt) ${pct(last.pOpt)}`;
        const noisy = points.filter((p) => p.noisyShots).pop();
        if (noisy) $('s-note').textContent = `noisy sampling: ${noisy.noisyShots} / ${noisy.of} shots`;
      },
      onNote: (text) => { if (!trace.length || /noisy|transpil/.test(text)) $('s-note').textContent = text; },
    });
    Q.sim = r;
    drawTrace(r.trace, r);
    renderSimulation(r);
    refreshNeedsAngles();
    recordRun({
      kind: 'qaoa', label: `QAOA p=${r.reps} · ${r.noiseDevice ? 'noisy ' + r.noiseDevice : 'ideal'}`,
      platform: r.noiseDevice ? `Aer + ${r.noiseDevice} noise model` : 'Aer statevector',
      scores: r.scores, seconds: r.optimiseSeconds,
      note: `${r.evaluations} evals · ⟨E⟩ ${fmt(r.expectationObjective)} · P(opt) ${pct(r.pOptimum)}`,
      qpu: '—',
    });
    if (Q.landscape && r.reps === 1) drawLandscape();
  } catch (err) {
    if (err.message !== 'stopped') qError('QAOA simulation failed: ' + err.message);
  } finally {
    simRunning(false);
  }
}

function simRunning(on, note) {
  busy('s-busy', on, note);
  $('s-run').disabled = on;
  $('s-landscape').disabled = on;
  $('s-stop').classList.toggle('hidden', !on);
}

function drawTrace(trace, result) {
  if (!trace.length) return;
  const opt = result ? result.optimum : null;
  const series = [{ name: '⟨E⟩ of the QAOA state', color: 'var(--series-1)',
                    points: trace.map((p) => [p.eval, p.energy, `restart ${p.restart + 1} · P(opt) ${pct(p.pOpt)}`]) }];
  if (trace.some((p) => Math.abs(p.value - p.energy) > 1e-9)) {
    series.push({ name: 'optimised objective (CVaR / sampled)', color: 'var(--series-2)',
                  points: trace.map((p) => [p.eval, p.value]) });
  }
  const refs = [];
  if (opt) {
    refs.push({ y: opt.energy, label: 'optimum' });
    refs.push({ y: opt.uniform.meanEnergy, label: 'random' });
  }
  lineChart($('s-trace'), series, { refs, xLabel: 'objective-function evaluation', xName: 'eval',
                                     legend: $('s-trace-legend'), label: 'QAOA optimiser convergence' });
}

async function runLandscape() {
  if (!qubo.model) return;
  $('s-landscape-panel').classList.remove('hidden');
  simRunning(true, 'computing the landscape…');
  try {
    Q.landscape = await runJob('qaoa-landscape', quboPayload({}), {
      onPoints: (points) => {
        const last = points[points.length - 1];
        if (last && last.row) $('s-note').textContent = `landscape row ${last.row} / ${last.of}`;
      },
      onNote: (text) => { $('s-note').textContent = text; },
    });
    drawLandscape();
  } catch (err) {
    if (err.message !== 'stopped') qError('Landscape failed: ' + err.message);
  } finally {
    simRunning(false);
  }
}

function divergingColor(t) {
  // t in [-1, 1]: blue (below random guessing) - grey - red (above)
  const dark = isDark();
  const neutral = dark ? [56, 56, 53] : [240, 239, 236];
  const negative = dark ? [57, 135, 229] : [16, 66, 129];
  const positive = dark ? [230, 103, 103] : [208, 59, 59];
  const end = t < 0 ? negative : positive;
  const m = Math.min(1, Math.abs(t));
  return `rgb(${[0, 1, 2].map((i) => Math.round(neutral[i] + (end[i] - neutral[i]) * m)).join(',')})`;
}

function drawLandscape() {
  const L = Q.landscape;
  if (!L) return;
  const canvas = $('s-landscape-canvas');
  const host = canvas.parentElement;
  const size = Math.max(240, Math.min(host.clientWidth || 420, 460));
  const pad = { l: 40, b: 30, t: 6, r: 6 };
  const W = size, H = Math.round(size * 0.8);
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr;
  canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = cssVar('--surface-1');
  ctx.fillRect(0, 0, W, H);
  const nb = L.betas.length, ng = L.gammas.length;
  const cw = (W - pad.l - pad.r) / nb, ch = (H - pad.t - pad.b) / ng;
  const ref = L.uniformEnergy;
  const below = Math.max(1e-9, ref - Math.min(...L.energy.flat()));
  const above = Math.max(1e-9, Math.max(...L.energy.flat()) - ref);
  for (let g = 0; g < ng; g++) {
    for (let b = 0; b < nb; b++) {
      const e = L.energy[g][b];
      const t = e < ref ? -(ref - e) / below : (e - ref) / above;
      ctx.fillStyle = divergingColor(t);
      ctx.fillRect(pad.l + b * cw, H - pad.b - (g + 1) * ch, Math.ceil(cw), Math.ceil(ch));
    }
  }
  const betaMax = L.betas[nb - 1] + (L.betas[1] - L.betas[0]);
  const gammaMax = L.gammas[ng - 1];
  const toX = (beta) => pad.l + (beta / betaMax) * (W - pad.l - pad.r);
  const toY = (gamma) => H - pad.b - (gamma / gammaMax) * (H - pad.t - pad.b - ch) - ch / 2;
  const placed = [];
  const marker = (beta, gamma, color, label) => {
    const x = toX(((beta % Math.PI) + Math.PI) % Math.PI) + cw / 2, y = toY(gamma);
    ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
    ctx.fillStyle = cssVar('--surface-1'); ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    // a second label next to the first would collide: stack it below instead
    const near = placed.some(([px, py]) => Math.abs(px - x) < 70 && Math.abs(py - y) < 16);
    const ly = near ? Math.max(12, y - 6) + 14 : Math.max(12, y - 6);
    ctx.font = '11px system-ui, sans-serif';
    const tx = Math.min(x + 9, W - ctx.measureText(label).width - 4);
    ctx.lineWidth = 3;
    ctx.strokeStyle = cssVar('--surface-1');
    ctx.strokeText(label, tx, ly);
    ctx.fillStyle = cssVar('--text-primary');
    ctx.fillText(label, tx, ly);
    placed.push([x, ly]);
  };
  marker(L.best.beta, L.best.gamma, cssVar('--text-primary'), 'grid best');
  if (Q.sim && Q.sim.reps === 1) {
    const [beta, gamma] = Q.sim.parameters;
    if (gamma >= 0 && gamma <= gammaMax) marker(beta, gamma, cssVar('--series-2'), 'optimiser');
  }
  ctx.fillStyle = cssVar('--text-muted');
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ['0', 'π/4', 'π/2', '3π/4', 'π'].forEach((label, i) =>
    ctx.fillText(label, pad.l + (i / 4) * (W - pad.l - pad.r), H - pad.b + 14));
  ctx.fillText('β (mixer angle)', pad.l + (W - pad.l - pad.r) / 2, H - 4);
  ctx.textAlign = 'right';
  [0, 0.5, 1].forEach((f) => ctx.fillText(fmt(f * gammaMax, 2), pad.l - 4, H - pad.b - f * (H - pad.t - pad.b) + 4));
  ctx.save();
  ctx.translate(10, (H - pad.b) / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center';
  ctx.fillText('γ (cost angle)', 0, 0);
  ctx.restore();
  ctx.textAlign = 'left';
  Q.landscapeGeom = { pad, cw, ch, W, H, nb, ng };

  const legend = $('s-landscape-legend');
  legend.textContent = '';
  const ramp = el('div', 'legend-ramp');
  const swatches = el('div', 'legend-swatches');
  [-1, -0.6, -0.3, 0, 0.3, 0.6, 1].forEach((t) => { const i = el('i'); i.style.background = divergingColor(t); swatches.appendChild(i); });
  ramp.append(el('span', null, `⟨E⟩ ${fmt(ref - below)}`), swatches, el('span', null, fmt(ref + above)));
  legend.appendChild(ramp);
  legend.appendChild(el('span', 'hint', `grey = random guessing (${fmt(ref)}) · click to start from a point`));
  $('s-landscape-note').textContent =
    `Best grid point β = ${L.best.beta.toFixed(3)}, γ = ${L.best.gamma.toFixed(3)}: ⟨E⟩ ${fmt(L.best.energy)} ` +
    `(optimum ${fmt(L.optimumEnergy)}), P(optimum) ${pct(L.best.pOptimum)}. Computed with a NumPy ` +
    `statevector in ${fmt(L.seconds)}s and checked against Qiskit Aer at that point (|Δ| = ` +
    `${L.aerCheck === null ? 'n/a' : L.aerCheck.toExponential(1)}).`;
}

function landscapeCell(event) {
  const L = Q.landscape, G = Q.landscapeGeom;
  if (!L || !G) return null;
  const rect = $('s-landscape-canvas').getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  const b = Math.floor((x - G.pad.l) / G.cw), g = Math.floor((G.H - G.pad.b - y) / G.ch);
  if (b < 0 || g < 0 || b >= G.nb || g >= G.ng) return null;
  return { b, g, x, y };
}

function landscapeHover(event) {
  const cell = landscapeCell(event);
  const tip = $('s-landscape-tip');
  if (!cell) { tip.classList.add('hidden'); return; }
  const L = Q.landscape;
  tip.textContent = `β ${L.betas[cell.b].toFixed(3)} · γ ${L.gammas[cell.g].toFixed(3)} · ⟨E⟩ ` +
    `${fmt(L.energy[cell.g][cell.b], 4)} · P(opt) ${pct(L.pOptimum[cell.g][cell.b])} · ` +
    `P(feasible) ${pct(L.pFeasible[cell.g][cell.b])}`;
  tip.classList.remove('hidden');
  tip.style.left = Math.min(cell.x + 12, $('s-landscape-canvas').clientWidth - 240) + 'px';
  tip.style.top = (cell.y + 12) + 'px';
}

function landscapeClick(event) {
  const cell = landscapeCell(event);
  if (!cell) return;
  const L = Q.landscape;
  $('c-reps').value = '1';
  $('s-init').value = `${L.betas[cell.b].toFixed(4)}, ${L.gammas[cell.g].toFixed(4)}`;
  refreshNeedsAngles();
}

function renderSimulation(r) {
  $('s-result').classList.remove('hidden');
  const row = $('s-stats');
  row.textContent = '';
  const uni = r.optimum.uniform;
  row.appendChild(statTile('Expected objective', fmt(r.expectationObjective),
    `⟨E⟩ ${fmt(r.expectation)} · random guessing ${fmt(uni.meanEnergy + qubo.model.offset)}`, true));
  row.appendChild(statTile('Approximation ratio', r.approximationRatio.toFixed(3),
    `random guessing ${r.uniformApproximationRatio.toFixed(3)} · 1 = optimum`));
  row.appendChild(statTile('P(optimum)', pct(r.pOptimum),
    `${uni.optimumFraction ? fmt(r.pOptimum / uni.optimumFraction) + '× random' : ''}` +
    (r.tts.shotsFor99 ? ` · ${r.tts.shotsFor99.toLocaleString()} shots for 99%` : '')));
  row.appendChild(statTile('P(feasible)', pct(r.pFeasible), `random ${pct(uni.feasibleFraction)}`));
  row.appendChild(statTile('Best sampled', fmt(r.scores.bestObjective),
    `${r.scores.shots.toLocaleString()} shots` + (r.scores.plan ? ` · plan ${r.scores.plan.objective} moves` : '')));
  row.appendChild(statTile('Optimisation', `${fmt(r.optimiseSeconds)}s`,
    `${r.evaluations} evals · ${(1000 * r.secondsPerEvaluation).toFixed(1)} ms each · ` +
    r.parameterNames.map((name, i) => `${name} = ${r.parameters[i].toFixed(3)}`).join(', ')));

  // the catch-all "everything higher" bucket would dwarf the levels that matter
  const rest = r.levels.find((l) => l.rest);
  $('s-levels-rest').textContent = rest
    ? `Higher levels (objective ≥ ${fmt(rest.energy + qubo.model.offset)}) hold ${pct(rest.qaoa)} of the ` +
      `QAOA probability, against ${pct(rest.uniform)} for random guessing.` : '';
  const levels = r.levels.filter((l) => !l.rest).map((l) => ({
    label: l.rest ? `≥${fmt(l.energy + qubo.model.offset)}` : fmt(l.objective),
    values: [l.qaoa, l.uniform],
    mark: Math.abs(l.energy - r.optimum.energy) < 1e-9 ? 'optimum' : null,
    tip: `<strong>objective ${l.rest ? '≥ ' + fmt(l.energy + qubo.model.offset) : fmt(l.objective)}</strong><br>` +
         `QAOA ${pct(l.qaoa)} (feasible ${pct(l.feasibleQaoa)})<br>random guessing ${pct(l.uniform)}` +
         (l.uniform > 0 ? `<br>${fmt(l.qaoa / l.uniform)}× amplified` : ''),
  }));
  columnChart($('s-levels'), levels, {
    series: [{ name: 'QAOA state', color: 'var(--series-1)' },
             { name: 'random guessing', color: 'var(--series-2)' }],
    percent: true, legend: $('s-levels-legend'), xLabel: 'objective (energy + offset), lowest levels first',
    label: 'probability per energy level',
  });

  sampleTable($('s-states'), r.states, `QAOA p=${r.reps}`, { probability: true });
  $('s-sampled-title').textContent = `Sampled shots — ${r.sampling.source}` +
    (r.sampling.partial ? ' (stopped early)' : '');
  energyHistogram($('s-hist'), $('s-hist-legend'), r.scores);
  sampleTable($('s-samples'), r.scores.samples.slice(0, 15), `QAOA p=${r.reps} sample`);
  simInterpretation(r);
}

function simInterpretation(r) {
  const box = $('s-interpretation');
  box.textContent = '';
  const uni = r.optimum.uniform;
  const ip = ipObjective();
  const p = el('p');
  p.innerHTML = `After optimisation the QAOA state has expected objective <strong>${fmt(r.expectationObjective)}</strong> ` +
    `against ${fmt(uni.meanEnergy + qubo.model.offset)} for random guessing and ${fmt(r.optimum.objective)} at the optimum ` +
    `— approximation ratio <strong>${r.approximationRatio.toFixed(3)}</strong> (random: ${r.uniformApproximationRatio.toFixed(3)}).`;
  box.appendChild(p);
  const list = el('ul');
  const amp = uni.optimumFraction ? r.pOptimum / uni.optimumFraction : null;
  list.appendChild(el('li', null,
    `The optimum is measured with probability ${pct(r.pOptimum)}` +
    (amp ? ` — ${fmt(amp)}× more often than by guessing` : '') +
    (r.tts.shotsFor99 ? `; ${r.tts.shotsFor99.toLocaleString()} shots give a 99% chance of seeing it at least once.` : '.')));
  list.appendChild(el('li', null,
    `${pct(r.pFeasible)} of the probability is on feasible selections (random: ${pct(uni.feasibleFraction)}). ` +
    'The one-hot penalties are what QAOA mostly learns to respect at low depth; the relocation cost comes second.'));
  list.appendChild(el('li', null,
    `Depth p = ${r.reps} gives the optimiser ${2 * r.reps} angles. Higher p can concentrate more probability on ` +
    'the optimum but deepens the circuit — see the estimates for what that costs in fidelity on hardware.'));
  if (r.noiseDevice) {
    const d = r.sampling.device;
    list.appendChild(el('li', null,
      `Final sampling ran through the ${r.noiseDevice} noise model: best sampled objective ` +
      `${fmt(r.scores.bestObjective)}, ${pct(r.scores.feasibleFraction)} feasible shots` +
      (r.scores.optimumFraction !== undefined ? `, ${pct(r.scores.optimumFraction)} at the optimum` : '') +
      ` (ideal: ${pct(r.pOptimum)}). Estimated success probability of the routed circuit: ${d ? pct(d.esp) : '—'}.`));
  }
  if (ip !== null) {
    list.appendChild(el('li', null, r.scores.bestObjective <= ip + 1e-9
      ? `Among the sampled shots is a selection at the classical IP optimum (${ip} relocations).`
      : `No sampled shot reached the IP optimum of ${ip}; the best was ${fmt(r.scores.bestObjective)}. ` +
        'Classical solvers remain far ahead at this size — QAOA here is a demonstration of the pipeline.'));
  }
  box.appendChild(list);
  if (r.scores.plan) {
    const button = el('button', 'btn btn-primary', `Play the best sampled plan (${r.scores.plan.objective} moves)`);
    button.type = 'button';
    button.addEventListener('click', () => showPlan(r.scores.plan, `QAOA p=${r.reps} sample`));
    box.appendChild(button);
  }
  const note = contextNote(r);
  if (note) box.appendChild(el('p', 'help', note));
}

/* ------------------------------------------------------------------ IBM */
function ibmCreds() {
  return { token: $('i-token').value.trim() || null, instance: $('i-instance').value.trim() || null,
           save: $('i-save').checked };
}

function fillBackends(backends) {
  const sel = $('i-backend');
  const keep = sel.value;
  sel.textContent = '';
  const live = el('optgroup');
  live.label = 'IBM Quantum Platform (needs an account)';
  const auto = el('option', null, 'least busy operational device');
  auto.value = '';
  live.appendChild(auto);
  (backends || []).forEach((b) => {
    const o = el('option', null, `${b.name} — ${b.qubits} qubits` +
      (b.pending !== undefined ? ` · ${b.pending} jobs queued` : '') +
      (b.operational === false ? ' · not operational' : ''));
    o.value = b.name;
    o.disabled = b.operational === false || (qubo.model && b.qubits < qubo.model.stats.columns);
    live.appendChild(o);
  });
  sel.appendChild(live);
  const local = el('optgroup');
  local.label = 'Local test of the Runtime path (no account, noisy simulation here)';
  (Q.status ? Q.status.devices : []).forEach((d) => {
    const o = el('option', null, `local: ${d.name} snapshot (${d.qubits} qubits)`);
    o.value = 'local:' + d.id;
    local.appendChild(o);
  });
  sel.appendChild(local);
  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
}

async function ibmConnect() {
  busy('i-connect-busy', true);
  $('i-connect').disabled = true;
  try {
    const r = await qapi('ibm-backends', { creds: ibmCreds() });
    fillBackends(r.backends);
    Q.ibmConnected = true;
    $('i-account').className = 'pill pill-good';
    $('i-account').textContent = `connected · ${r.backends.length} backends`;
  } catch (err) {
    qError('Could not reach IBM Quantum: ' + err.message);
  } finally {
    busy('i-connect-busy', false);
    $('i-connect').disabled = false;
  }
}

function ibmPayload(extra) {
  return quboPayload({ creds: ibmCreds(), backend: $('i-backend').value,
                       reps: Q.build ? Q.build.reps : val('c-reps') || 1,
                       shots: val('i-shots') || 4096, ...(extra || {}) });
}

async function ibmCheck() {
  if (!qubo.model) return;
  busy('i-busy', true, 'transpiling against the backend…');
  try {
    const d = await qapi('ibm-check', ibmPayload());
    const box = $('i-check-result');
    box.textContent = '';
    const p = el('p');
    p.innerHTML = `<strong>${escapeHtml(d.device)}</strong>${d.local ? ' (local snapshot)' : ''}: the circuit ` +
      `transpiles to <strong>${d.physicalQubits}</strong> physical qubits, depth <strong>${d.depth}</strong>, ` +
      `<strong>${d.twoQubit}</strong> two-qubit gates; circuit time ${seconds(d.durationS)}, estimated ` +
      `success probability <strong>${pct(d.esp)}</strong>. A ${d.shots}-shot job needs about ` +
      `<strong>${seconds(d.samplingS)}</strong> of QPU time, plus queueing. No job was sent.`;
    box.appendChild(p);
  } catch (err) {
    qError('Check failed: ' + err.message);
  } finally {
    busy('i-busy', false);
  }
}

async function ibmSubmit() {
  const params = currentAngles();
  if (!qubo.model || !params) return;
  const backend = $('i-backend').value;
  const local = backend.startsWith('local:');
  if (!local && !window.confirm(`Submit a ${val('i-shots') || 4096}-shot QAOA sampling job to ` +
      `${backend || 'the least busy IBM device'}? It uses QPU time on your IBM Quantum account.`)) {
    return;
  }
  busy('i-busy', true, local ? 'running the job locally (noisy simulation)…' : 'transpiling and submitting…');
  $('i-submit').disabled = true;
  try {
    const record = await runJob('ibm-submit', ibmPayload({ params }));
    $('i-job-id').value = record.jobId;
    await renderIbmJobs();
    await ibmFetch(record.jobId);
  } catch (err) {
    qError('Submission failed: ' + err.message);
  } finally {
    busy('i-busy', false);
    refreshNeedsAngles();
  }
}

async function renderIbmJobs() {
  if (!Q.status || !Q.status.available) return;
  try {
    const { jobs } = await qapi('ibm-jobs');
    const table = $('i-jobs');
    table.textContent = '';
    if (!jobs.length) {
      table.appendChild(el('caption', 'hint', 'No jobs submitted from this GUI yet.'));
      return;
    }
    const thead = el('thead'), hr = el('tr');
    ['Job id', 'Backend', 'Submitted', 'QUBO', 'p', 'Shots', ''].forEach((h) => hr.appendChild(el('th', null, h)));
    thead.appendChild(hr); table.appendChild(thead);
    const body = el('tbody');
    jobs.slice(0, 20).forEach((j) => {
      const tr = el('tr', 'is-static');
      const mine = qubo.model && (j.qubo === qubo.model.path.split('/').pop());
      tr.appendChild(el('td', 'mono', j.jobId));
      tr.appendChild(el('td', null, j.backend + (j.local ? ' (local)' : '')));
      tr.appendChild(el('td', null, j.submitted));
      tr.appendChild(el('td', mine ? null : 'muted', j.qubo));
      tr.appendChild(el('td', null, String(j.reps)));
      tr.appendChild(el('td', null, String(j.shots)));
      const td = el('td');
      const b = el('button', 'btn btn-ghost btn-small', 'Fetch');
      b.type = 'button';
      b.disabled = !mine;
      b.title = mine ? 'Fetch status / results' : 'load this job\'s QUBO first: its bits only decode against it';
      b.addEventListener('click', () => { $('i-job-id').value = j.jobId; ibmFetch(j.jobId); });
      td.appendChild(b);
      tr.appendChild(td);
      body.appendChild(tr);
    });
    table.appendChild(body);
  } catch (err) { /* the table just stays as it was */ }
}

async function ibmFetch(jobId, attempt) {
  jobId = (jobId || $('i-job-id').value).trim();
  if (!jobId || !qubo.model) return;
  busy('i-busy', true, `fetching job ${jobId}…`);
  try {
    const r = await qapi('ibm-job', ibmPayload({ jobId, oldSign: $('i-old-sign').checked }));
    if (!r.scores) {
      $('i-check-result').textContent = `Job ${jobId} on ${r.backend || '?'}: ${r.status}.` +
        (r.local ? ' (local job)' : ' Fetch again later — queued jobs can take minutes to hours.');
      if (r.local && (attempt || 0) < 60 && !/ERROR|CANCEL/.test(r.status)) {
        setTimeout(() => ibmFetch(jobId, (attempt || 0) + 1), 2000);
      }
      return;
    }
    Q.ibmResult = r;
    renderIbmResult(r);
    recordRun({
      kind: 'ibm', hardware: !r.local, label: `QAOA on ${r.backend}${r.local ? ' (local)' : ''}`,
      platform: r.local ? 'Runtime local mode' : 'IBM Quantum hardware', scores: r.scores,
      seconds: r.usage && r.usage.seconds, note: `job ${jobId}`,
      qpu: r.usage && r.usage.quantum_seconds !== undefined ? `${r.usage.quantum_seconds} s (billed)` : '—',
      id: 'ibm:' + jobId,
    });
  } catch (err) {
    qError('Could not fetch the job: ' + err.message);
  } finally {
    busy('i-busy', false);
  }
}

function renderIbmResult(r) {
  $('i-result').classList.remove('hidden');
  const row = $('i-stats');
  row.textContent = '';
  scoreTiles(row, r.scores, r.usage && r.usage.quantum_seconds !== undefined
    ? [statTile('QPU usage', `${r.usage.quantum_seconds} s`, 'billed quantum seconds')] : []);
  energyHistogram($('i-hist'), $('i-hist-legend'), r.scores);
  sampleTable($('i-samples'), r.scores.samples.slice(0, 15), `QAOA on ${r.backend}`);
  const box = $('i-interpretation');
  box.textContent = '';
  const s = r.scores, sim = Q.sim;
  const list = el('ul');
  list.appendChild(el('li', null, `Decoding: ${r.decoding}.`));
  list.appendChild(el('li', null,
    `Best shot: objective ${fmt(s.bestObjective)}; ${pct(s.feasibleFraction)} feasible shots; mean energy ` +
    `${fmt(s.meanEnergy)}` + (s.optimum && s.optimum.uniform ? ` (random guessing ${fmt(s.optimum.uniform.meanEnergy)})` : '') + '.'));
  if (sim) {
    list.appendChild(el('li', null,
      `The ideal simulation of the same circuit predicted P(optimum) ${pct(sim.pOptimum)} and ${pct(sim.pFeasible)} ` +
      `feasible; the device gave ${pct(s.optimumFraction)} and ${pct(s.feasibleFraction)}. The gap is the hardware noise.`));
  }
  if (s.optimum && s.optimum.uniform && s.meanEnergy >= s.optimum.uniform.meanEnergy) {
    list.appendChild(el('li', 'qubo-bad',
      'Mean energy is no better than random guessing: noise has washed out the QAOA signal.'));
  }
  box.appendChild(list);
  if (s.plan) {
    const b = el('button', 'btn btn-primary', `Play the best plan (${s.plan.objective} moves)`);
    b.type = 'button';
    b.addEventListener('click', () => showPlan(s.plan, `QAOA on ${r.backend}`));
    box.appendChild(b);
  }
}

/* ------------------------------------------------------------- summary */
function recordRun(run) {
  const s = run.scores;
  const entry = {
    id: run.id || `${run.kind}-${Date.now()}`,
    kind: run.kind, hardware: !!run.hardware, label: run.label, platform: run.platform,
    energy: s.bestEnergy, objective: s.bestObjective,
    plan: s.plan ? s.plan.objective : null, filled: s.best ? s.best.filled : null,
    pOpt: s.optimumFraction, feasible: s.feasibleFraction, shots: s.shots,
    seconds: run.seconds, qpu: run.qpu, note: run.note, planPayload: s.plan || null,
    at: new Date().toLocaleTimeString(),
  };
  Q.runs = Q.runs.filter((r) => r.id !== entry.id);
  Q.runs.push(entry);
  renderPipeline();
  if (Q.sub === 'summary') renderSummary();
}

function renderSummary() {
  if (!qubo.model) return;
  const ip = ipObjective();
  const meta = qubo.model.meta;
  const rows = [];
  if (ip !== null) {
    rows.push({ label: 'Classical IP (Gurobi)', value: ip, color: 'var(--text-secondary)',
                tip: `<strong>Classical IP</strong><br>${ip} relocations` +
                     (meta && meta.provenOptimal ? ' · proven optimal' : '') });
  }
  Q.runs.forEach((r) => rows.push({
    label: r.label, value: r.plan, color: r.hardware ? 'var(--series-2)' : 'var(--series-1)',
    empty: 'no feasible plan',
    tip: `<strong>${escapeHtml(r.label)}</strong><br>best objective ${fmt(r.objective)}<br>` +
         `plan ${r.plan === null ? '—' : r.plan + ' moves'}${r.filled ? ` (${r.filled} filled)` : ''}<br>` +
         `P(optimum) ${pct(r.pOpt)} · feasible ${pct(r.feasible)}`,
  }));
  chartLegend($('r-legend'), [
    ...(ip !== null ? [{ name: 'classical IP', color: 'var(--text-secondary)' }] : []),
    { name: 'simulation / classical sampler', color: 'var(--series-1)' },
    { name: 'quantum hardware', color: 'var(--series-2)' }]);
  dotPlot($('r-chart'), rows, { ref: ip, xLabel: 'relocations in the best playable plan (lower is better; line = IP optimum)',
                                label: 'best plan per run' });

  const table = $('r-table');
  table.textContent = '';
  const thead = el('thead'), hr = el('tr');
  ['Run', 'Platform', 'Best objective', 'Best plan', 'P(optimum)', 'Feasible', 'Shots / reads',
   'Time here', 'QPU time', 'Notes', ''].forEach((h) => hr.appendChild(el('th', null, h)));
  thead.appendChild(hr); table.appendChild(thead);
  const body = el('tbody');
  if (ip !== null) {
    const tr = el('tr', 'is-static');
    [['Classical IP', null], ['Gurobi, this machine', null], [String(ip), null],
     [`${ip} moves`, null], ['proven', null], ['—', null], ['—', null],
     [meta && meta.solveSeconds ? `${fmt(meta.solveSeconds)}s` : '—', null], ['—', null],
     [meta && meta.provenOptimal ? 'proven optimal' : '', null], ['', null]]
      .forEach(([t]) => tr.appendChild(el('td', null, t)));
    body.appendChild(tr);
  }
  Q.runs.forEach((r) => {
    const tr = el('tr', 'is-static');
    tr.appendChild(el('td', null, r.label));
    tr.appendChild(el('td', null, r.platform));
    tr.appendChild(el('td', ip !== null && r.objective <= ip + 1e-9 ? 'qubo-ok' : null, fmt(r.objective)));
    tr.appendChild(el('td', null, r.plan === null ? '—' : `${r.plan} moves${r.filled ? ` (${r.filled} filled)` : ''}`));
    tr.appendChild(el('td', null, pct(r.pOpt)));
    tr.appendChild(el('td', null, pct(r.feasible)));
    tr.appendChild(el('td', null, r.shots ? r.shots.toLocaleString() : '—'));
    tr.appendChild(el('td', null, r.seconds ? `${fmt(r.seconds)}s` : '—'));
    tr.appendChild(el('td', null, r.qpu || '—'));
    tr.appendChild(el('td', 'muted', r.note || ''));
    const td = el('td');
    if (r.planPayload) {
      const b = el('button', 'btn btn-ghost btn-small', 'Play');
      b.type = 'button';
      b.addEventListener('click', () => showPlan(r.planPayload, r.label));
      td.appendChild(b);
    }
    tr.appendChild(td);
    body.appendChild(tr);
  });
  table.appendChild(body);

  const box = $('r-interpretation');
  box.textContent = '';
  if (!Q.runs.length) {
    box.appendChild(el('p', null, 'Run a sampler under Quantum annealing or a QAOA simulation to compare it here.'));
    return;
  }
  const list = el('ul');
  const atOpt = Q.runs.filter((r) => ip !== null && r.objective <= ip + 1e-9);
  if (ip !== null) {
    list.appendChild(el('li', null,
      `${atOpt.length} of ${Q.runs.length} run(s) found a sample at the IP optimum (objective ${ip})` +
      (atOpt.length ? ` (${atOpt.map((r) => r.label).join('; ')}).` : '.')));
  }
  const anneal = Q.runs.filter((r) => r.kind === 'anneal');
  const qaoa = Q.runs.filter((r) => r.kind !== 'anneal');
  if (anneal.length) {
    const bestP = anneal.reduce((a, b) => ((b.pOpt || 0) > (a.pOpt || 0) ? b : a));
    list.appendChild(el('li', null,
      `Annealing-style samplers: the most reliable was ${bestP.label} with ${pct(bestP.pOpt)} of reads at the optimum.`));
  }
  if (qaoa.length) {
    const best = qaoa.reduce((a, b) => ((b.pOpt || 0) > (a.pOpt || 0) ? b : a));
    list.appendChild(el('li', null,
      `Gate-based QAOA: best hit rate ${pct(best.pOpt)} (${best.label}). Low-depth QAOA spreads its probability ` +
      'widely, so it needs many shots where an annealer or tabu search finds the optimum in a handful of reads.'));
  }
  list.appendChild(el('li', null,
    `Scale: this QUBO has ${qubo.model.stats.columns} variables. Exact statevector simulation doubles in cost per ` +
    'qubit (feasible here up to 24), annealing embeds a few hundred variables on today\'s QPUs, while the classical ' +
    'IP solves far larger instances to proven optimality — the quantum routes are shown for comparison, not speed.'));
  box.appendChild(list);
}

function exportReport() {
  const report = {
    qubo: qubo.model ? qubo.model.path : null,
    exportedFrom: qubo.model ? qubo.model.meta : null,
    ipObjective: ipObjective(),
    generated: new Date().toISOString(),
    runs: Q.runs.map(({ planPayload, ...rest }) => ({ ...rest,
      plan: planPayload ? planPayload.relocations : null })),
    qaoa: Q.sim ? { reps: Q.sim.reps, parameters: Q.sim.parameters, pOptimum: Q.sim.pOptimum,
                    expectation: Q.sim.expectation, approximationRatio: Q.sim.approximationRatio } : null,
    estimate: Q.estimate,
    embedding: Q.embedding ? { topology: Q.embedding.topology, physical: Q.embedding.physical,
                               maxChain: Q.embedding.maxChain, chains: Q.embedding.chains } : null,
  };
  const blob = new Blob([JSON.stringify(report, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${(qubo.model ? qubo.model.path : 'qubo').replace(/[/.]/g, '-')}-report.json`;
  document.body.appendChild(a); a.click(); a.remove();
}

/* --------------------------------------------------------------- wiring */
document.querySelectorAll('.main-tab').forEach((t) =>
  t.addEventListener('click', () => setView(t.dataset.view)));
document.querySelectorAll('#quantum-subnav .seg-btn').forEach((b) =>
  b.addEventListener('click', () => setSub(b.dataset.sub)));
$('a-embed').addEventListener('click', embedProblem);
$('a-topology').addEventListener('change', updateTiming);
$('a-method').addEventListener('change', annealMethodChanged);
['a-reads', 'a-anneal-time'].forEach((id) => $(id).addEventListener('input', updateTiming));
$('a-run').addEventListener('click', runAnneal);
$('a-embed-canvas').addEventListener('mousemove', embedHover);
$('a-embed-canvas').addEventListener('mouseleave', () => {
  $('a-embed-tip').classList.add('hidden');
  if (Q.hoverVar !== null) { Q.hoverVar = null; drawEmbedding(); }
});
document.querySelectorAll('#a-embed-zoom .seg-btn').forEach((b) => b.addEventListener('click', () => {
  Q.zoom = b.dataset.zoom;
  document.querySelectorAll('#a-embed-zoom .seg-btn').forEach((x) => x.classList.toggle('is-active', x === b));
  drawEmbedding();
}));
$('c-build').addEventListener('click', buildCircuit);
document.querySelectorAll('#c-diagram-kind .seg-btn').forEach((b) =>
  b.addEventListener('click', () => setDiagram(b.dataset.kind)));
$('c-diagram-device').addEventListener('change', () => setDiagram('transpiled'));
$('c-diagram').addEventListener('click', () => $('c-diagram').classList.toggle('is-zoomed'));
document.querySelectorAll('[data-export]').forEach((a) => a.addEventListener('click', exportClick));
$('e-run').addEventListener('click', runEstimate);
$('s-run').addEventListener('click', runSimulation);
$('s-landscape').addEventListener('click', runLandscape);
$('s-stop').addEventListener('click', () => { cancelJob('qaoa-run'); cancelJob('qaoa-landscape'); });
$('s-init').addEventListener('input', refreshNeedsAngles);
$('s-landscape-canvas').addEventListener('mousemove', landscapeHover);
$('s-landscape-canvas').addEventListener('mouseleave', () => $('s-landscape-tip').classList.add('hidden'));
$('s-landscape-canvas').addEventListener('click', landscapeClick);
$('i-connect').addEventListener('click', ibmConnect);
$('i-check').addEventListener('click', ibmCheck);
$('i-submit').addEventListener('click', ibmSubmit);
$('i-fetch').addEventListener('click', () => ibmFetch());
$('r-export').addEventListener('click', exportReport);
let quantumResize = null;
window.addEventListener('resize', () => {
  clearTimeout(quantumResize);
  quantumResize = setTimeout(() => {
    if ($('view-quantum').classList.contains('hidden')) return;
    if (Q.embedding && Q.sub === 'anneal') drawEmbedding();
    if (Q.landscape) drawLandscape();
    if (Q.sub === 'summary') renderSummary();
  }, 200);
});
$('theme-select').addEventListener('change', () => {
  if (Q.embedding) drawEmbedding();
  if (Q.landscape) drawLandscape();
});

async function initQuantum() {
  try {
    Q.status = await qapi('status');
  } catch (err) {
    Q.status = { available: false, error: err.message, python: '?' };
  }
  renderToolkit();
  if (!Q.status.available) {
    $('quantum-badge').textContent = 'unavailable';
    return;
  }
  renderDeviceChoices();
  fillBackends([]);
  const ibm = Q.status.ibm;
  $('i-account').className = 'pill ' + (ibm.savedAccount ? 'pill-good' : 'pill-muted');
  $('i-account').textContent = ibm.savedAccount
    ? `saved account found (${ibm.channel})` : 'no saved account — paste an API key';
  if (Q.status.dwave.configured) $('a-token').placeholder = 'blank = use the configured Leap token';
  annealMethodChanged();
  refreshNeedsAngles();
}

initQuantum();
