/* HPC tab: run classical solves and classical simulations of the quantum
   algorithms as Slurm array jobs on FRIDA (or any Slurm cluster over SSH),
   follow them, fetch the results and feed them back into the other tabs.
   Back end: gui/hpc.py. Uses app.js / qubo.js / quantum.js helpers. */
'use strict';

const H = {
  info: null,
  mode: 'classical',
  jobs: [],
  results: null,
  preview: null,
  previewFile: 'sbatch',
  connected: false,
  timer: null,
};

const HPC_METHODS = [
  ['sa', 'Simulated annealing', true],
  ['sa-embedded', 'SA on the embedded problem', false],
  ['tabu', 'Tabu search', true],
  ['steepest', 'Steepest descent', false],
  ['tree', 'Tree decomposition (exact)', false],
  ['exact', 'Exhaustive (exact, ≤ max qubits)', false],
  ['random', 'Random baseline', true],
];

async function hapi(action, payload) {
  const url = '/api/hpc/' + action;
  const res = payload === undefined
    ? await fetch(url)
    : await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                         body: JSON.stringify(payload) });
  let data;
  try { data = await res.json(); } catch (err) { throw new Error(`${action}: HTTP ${res.status}`); }
  if (data && data.error) throw new Error(data.error);
  return data;
}

function hBusy(id, on) { $(id).classList.toggle('hidden', !on); }

function hNote(text, bad) {
  const note = $('h-conn-note');
  note.textContent = text || '';
  note.style.color = bad ? 'var(--target)' : '';
}

/* ------------------------------------------------------------ connection */
function configPayload() {
  return {
    transport: $('h-transport').value, host: $('h-host').value.trim(),
    remoteBase: $('h-base').value.trim(), account: $('h-account').value.trim(),
    gurobiLicense: $('h-license').value.trim(),
  };
}

function fillConfig(cfg) {
  $('h-transport').value = cfg.transport || 'ssh';
  $('h-host').value = cfg.host || 'login-frida';
  $('h-base').value = cfg.remoteBase || '~/brpq';
  $('h-account').value = cfg.account || '';
  $('h-license').value = cfg.gurobiLicense || '';
}

function setConnected(ok, label) {
  H.connected = ok;
  $('h-conn').className = 'pill ' + (ok ? 'pill-good' : 'pill-bad');
  $('h-conn').textContent = label;
  $('hpc-badge').textContent = ok ? 'connected' : 'not connected';
}

async function hpcCheck() {
  hBusy('h-conn-busy', true);
  hNote('');
  try {
    const r = await hapi('check', configPayload());
    const f = r.facts;
    setConnected(true, `${f.user}@${f.host}`);
    const host = $('h-facts');
    host.textContent = '';
    const yes = (v) => v === 'yes';
    host.append(
      statTile('Remote base', f.base || '—', f.transport === 'local' ? 'this machine' : 'cluster'),
      statTile('Slurm', yes(f.slurm) ? 'available' : 'not found',
               f.partitions ? `${f.partitions.length} partitions` : ''),
      statTile('Project copy', yes(f.repo) ? 'present' : 'missing',
               yes(f.repo) ? 're-copy after changes' : 'press "Copy project"'),
      statTile('Solver binary', yes(f.binary) ? 'built' : 'not yet', 'built by the first job'),
      statTile('CPU image', f.image_cpu || '—', 'Enroot squashfs'),
      statTile('GPU image', f.image_gpu || '—', 'needed for CuPy'),
      statTile('Gurobi licence', f.gurobi_license === 'missing' ? 'missing' : 'found',
               'needed for classical solves'),
    );
    const todo = [];
    if (f.transport !== 'local' && !yes(f.repo)) todo.push('copy the project');
    if (f.transport !== 'local' && (f.image_cpu || 'missing') === 'missing') todo.push('build the CPU image');
    if (f.gurobi_license === 'missing') todo.push('put a Gurobi licence (WLS or token server) at the path above');
    hNote(todo.length ? 'Next: ' + todo.join(', ') + '.' : 'Ready to submit jobs.');
  } catch (err) {
    setConnected(false, 'not connected');
    hNote(err.message, true);
  } finally {
    hBusy('h-conn-busy', false);
  }
}

async function hpcSync() {
  hBusy('h-conn-busy', true);
  try {
    const r = await hapi('sync', {});
    hNote(r.note || `Copied ${r.files} files (${(r.bytes / 1e6).toFixed(1)} MB) to ${r.repo}.`);
    await hpcCheck();
  } catch (err) {
    hNote('Copy failed: ' + err.message, true);
  } finally {
    hBusy('h-conn-busy', false);
  }
}

async function hpcBuildImage(variant) {
  const what = variant === 'gpu'
    ? 'the GPU image (Ubuntu 24.04, Gurobi, Ocean, Qiskit, CuPy for CUDA 12)'
    : 'the CPU image (Ubuntu 24.04, Gurobi, Ocean, Qiskit, Aer)';
  if (!window.confirm(`Submit a build job for ${what} on the dev partition? It takes 10–30 minutes.`)) return;
  hBusy('h-conn-busy', true);
  try {
    const r = await hapi('build-image', { variant });
    hNote(`Image build submitted as Slurm job ${r.slurmId}; follow it under Jobs.`);
    await hpcRefresh();
  } catch (err) {
    hNote('Image build failed: ' + err.message, true);
  } finally {
    hBusy('h-conn-busy', false);
  }
}

/* ------------------------------------------------------------- job form */
const MODE_HELP = {
  classical: 'One array task per selected test case: the Tanaka–Voß IP solved with Gurobi, '
    + 'and its QUBO exported for later quantum runs.',
  pipeline: 'One array task per selected test case: the classical solve and QUBO export, then '
    + 'every annealing sampler and QAOA depth chosen below on that QUBO — a benchmark sweep.',
  quantum: 'One array task per sampler and per QAOA depth, all on the QUBO loaded in the Quantum tab '
    + '(uploaded with the job). Large QUBOs that do not fit here go to big-memory or GPU nodes.',
};

function setMode(mode) {
  H.mode = mode;
  document.querySelectorAll('#h-mode .seg-btn').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.mode === mode));
  $('h-mode-help').textContent = MODE_HELP[mode];
  $('h-instances-block').classList.toggle('hidden', mode === 'quantum');
  $('h-qubo-block').classList.toggle('hidden', mode !== 'quantum');
  $('h-quantum-block').classList.toggle('hidden', mode === 'classical');
  updateQuboName();
  resourceNote();
}

function updateQuboName() {
  $('h-qubo-name').textContent = qubo.model
    ? `${qubo.model.path} (${(qubo.model.variables || []).length} variables)`
    : 'none loaded — load one in the Quantum tab first';
}

function fillInstances() {
  const sel = $('h-instances');
  const chosen = new Set([...sel.selectedOptions].map((o) => o.value));
  const filter = $('h-filter').value.trim().toLowerCase();
  sel.textContent = '';
  (state.instances || []).filter((i) => !i.error).forEach((i) => {
    if (filter && !i.name.toLowerCase().includes(filter) && !chosen.has(i.name)) return;
    const o = el('option', null, `${i.name} · ${i.stacks}×${i.fileTiers} · ${i.blocks} blocks`);
    o.value = i.name;
    o.selected = chosen.has(i.name);
    sel.appendChild(o);
  });
  countInstances();
}

function countInstances() {
  const n = $('h-instances').selectedOptions.length;
  $('h-count').textContent = `${n} selected`;
}

function fillChoices() {
  const parts = $('h-partition');
  parts.textContent = '';
  (H.info.partitions || []).forEach((p) => {
    const o = el('option', null, `${p.name} — max ${p.maxTime}`);
    o.value = p.name;
    parts.appendChild(o);
  });
  const methods = $('h-anneal-methods');
  methods.textContent = '';
  HPC_METHODS.forEach(([id, label, on]) => {
    const l = el('label', 'check inline');
    const box = el('input');
    box.type = 'checkbox'; box.value = id; box.checked = on;
    l.append(box, el('span', null, label));
    methods.appendChild(l);
  });
  fillNoise();
  partitionChanged();
}

function fillNoise() {
  const noise = $('h-noise');
  if (noise.options.length > 1 || !(Q.status && Q.status.devices)) return;
  Q.status.devices.forEach((d) => {
    const o = el('option', null, `Noisy: ${d.name} noise model (${d.family})`);
    o.value = d.id;
    noise.appendChild(o);
  });
}

function partitionChanged() {
  const p = (H.info.partitions || []).find((x) => x.name === $('h-partition').value);
  const gpu = $('h-gpu');
  const keep = gpu.value;
  gpu.textContent = '';
  gpu.appendChild(Object.assign(el('option', null, 'none'), { value: '' }));
  gpu.appendChild(Object.assign(el('option', null, 'any GPU'), { value: 'any' }));
  (p ? p.gpus : []).forEach((g) => {
    const mem = H.info.gpuMemoryGB[g];
    gpu.appendChild(Object.assign(el('option', null, `${g}${mem ? ` (${mem} GB)` : ''}`), { value: g }));
  });
  gpu.value = [...gpu.options].some((o) => o.value === keep) ? keep : '';
  if (p) $('h-time').placeholder = `default ${p.defaultTime}, max ${p.maxTime}`;
  resourceNote();
}

function resourceNote() {
  if (!H.info) return;
  const p = (H.info.partitions || []).find((x) => x.name === $('h-partition').value);
  const notes = [];
  if (p) notes.push(`${p.name}: ${p.note}.`);
  const gpu = $('h-gpu').value, backend = $('h-backend').value;
  if (backend === 'cupy' && !gpu) notes.push('CuPy needs a GPU — pick a GPU type.');
  if (gpu && gpu !== 'any' && H.info.gpuMemoryGB[gpu]) {
    const q = Math.floor(Math.log2(H.info.gpuMemoryGB[gpu] * 1e9 / 48));
    notes.push(`${gpu} holds a statevector of about ${q} qubits.`);
  }
  if (backend !== 'cupy') {
    const mem = Number($('h-mem').value) || 0;
    if (mem) notes.push(`${mem} GB of RAM holds about ${Math.floor(Math.log2(mem * 1e9 / 72))} qubits `
      + '(statevector, probabilities and the energy table).');
  }
  if (H.mode === 'classical' && gpu) notes.push('A classical solve does not use the GPU.');
  $('h-res-note').textContent = notes.join(' ');
}

function annealConfigs() {
  const methods = [...document.querySelectorAll('#h-anneal-methods input:checked')].map((b) => b.value);
  return methods.map((method) => ({
    method, reads: numberOrNull($('h-reads').value) || 1000,
    sweeps: numberOrNull($('h-sweeps').value) || 1000, topology: $('h-topology').value, seed: 1,
  }));
}

function qaoaConfigs() {
  const reps = $('h-reps').value.split(/[\s,;]+/).map(Number).filter((n) => Number.isInteger(n) && n >= 1);
  return [...new Set(reps)].map((p) => ({
    reps: p, restarts: numberOrNull($('h-restarts').value) || 1,
    maxiter: numberOrNull($('h-maxiter').value) || 100, shots: numberOrNull($('h-shots').value) || 4096,
    cvar: numberOrNull($('h-cvar').value) || 1, optimizer: $('h-optimizer').value,
    noiseDevice: $('h-noise').value || null, seed: 1,
  }));
}

function jobPayload() {
  const opts = currentOptions();
  delete opts.instance; delete opts.exportQubo; delete opts.bay; delete opts.name; delete opts.tiers;
  return {
    mode: H.mode, name: $('h-name').value.trim() || null,
    instances: [...$('h-instances').selectedOptions].map((o) => o.value),
    options: opts, exportQubo: true,
    anneal: H.mode === 'classical' ? [] : annealConfigs(),
    qaoa: H.mode === 'classical' ? [] : qaoaConfigs(),
    qubo: qubo.model ? qubo.model.path : null,
    instance: $('instance-select').value,
    resources: {
      partition: $('h-partition').value, time: $('h-time').value.trim(),
      cpus: numberOrNull($('h-cpus').value), mem: numberOrNull($('h-mem').value),
      gpu: $('h-gpu').value, backend: $('h-backend').value,
      exactLimit: numberOrNull($('h-exact').value), maxParallel: numberOrNull($('h-parallel').value) || 0,
    },
  };
}

async function hpcPreview() {
  try {
    H.preview = await hapi('preview', jobPayload());
    $('h-preview-box').classList.remove('hidden');
    $('h-preview-notes').textContent = [
      `${H.preview.spec.tasks.length} array task(s) in ${H.preview.jobdir}.`,
      ...(H.preview.notes || []),
      H.preview.files.length ? `Uploaded with it: ${H.preview.files.join(', ')}.` : '',
    ].join(' ');
    showPreviewFile(H.previewFile);
  } catch (err) {
    hpcError('Job preview failed: ' + err.message);
  }
}

function showPreviewFile(file) {
  H.previewFile = file;
  document.querySelectorAll('#h-preview-tabs .seg-btn').forEach((b) =>
    b.classList.toggle('is-active', b.dataset.file === file));
  if (!H.preview) return;
  $('h-preview-text').textContent = file === 'spec'
    ? JSON.stringify(H.preview.spec, null, 1) : H.preview[file];
}

async function hpcSubmit() {
  const payload = jobPayload();
  let prep;
  try {
    prep = await hapi('preview', payload);
  } catch (err) {
    hpcError('Cannot build the job: ' + err.message);
    return;
  }
  const r = prep.spec.resources;
  const msg = `Submit ${prep.spec.tasks.length} array task(s) to partition ${r.partition}`
    + ` (${r.cpus} CPUs, ${r.mem} GB${r.gpu ? ', GPU ' + r.gpu : ''}, ${r.time} each)?`
    + (prep.notes.length ? '\n\n' + prep.notes.join('\n') : '');
  if (!window.confirm(msg)) return;
  hBusy('h-submit-busy', true);
  try {
    const rec = await hapi('submit', payload);
    hpcError(null);
    $('h-name').value = '';
    await hpcRefresh();
    $('h-jobs').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return rec;
  } catch (err) {
    hpcError('Submit failed: ' + err.message);
  } finally {
    hBusy('h-submit-busy', false);
  }
}

function hpcError(message) {
  const note = $('h-res-note');
  note.style.color = '';
  if (!message) { resourceNote(); return; }
  note.textContent = message;
  note.style.color = 'var(--target)';
}

/* ------------------------------------------------------------------ jobs */
function stateText(j) {
  const counts = j.counts || {};
  const parts = Object.entries(counts).map(([k, v]) => `${v} ${k.toLowerCase()}`);
  return parts.length ? parts.join(', ') : (j.state || '?').toLowerCase();
}

function stateClass(state) {
  return state === 'DONE' ? 'pill-good' : state === 'FAILED' || state === 'CANCELLED' ? 'pill-bad'
    : state === 'RUNNING' ? 'pill-warn' : 'pill-muted';
}

function renderJobs() {
  const table = $('h-jobs');
  table.textContent = '';
  const head = el('tr');
  ['Job', 'What', 'Tasks', 'Slurm id', 'State', 'Results', 'Submitted', ''].forEach((h) =>
    head.appendChild(el('th', null, h)));
  table.appendChild(el('thead')).appendChild(head);
  const body = table.appendChild(el('tbody'));
  if (!H.jobs.length) {
    const tr = body.appendChild(el('tr'));
    const td = tr.appendChild(el('td', 'hint', 'No jobs yet.'));
    td.colSpan = 8;
    return;
  }
  H.jobs.forEach((j) => {
    const tr = body.appendChild(el('tr'));
    tr.appendChild(el('td', null, j.name));
    tr.appendChild(el('td', null, j.kind === 'image' ? `${j.variant} image build`
      : `${j.mode}${j.summary ? ' — ' + j.summary : ''}`));
    tr.appendChild(el('td', 'num', String(j.tasks || 1)));
    tr.appendChild(el('td', null, j.slurmId || '—'));
    const st = tr.appendChild(el('td'));
    st.appendChild(el('span', 'pill ' + stateClass(j.state), stateText(j)));
    tr.appendChild(el('td', 'num', j.kind === 'job'
      ? `${j.resultsReady ?? '–'} / ${j.tasks}${j.fetched ? ' · fetched' : ''}` : '—'));
    tr.appendChild(el('td', null, j.submitted || ''));
    const actions = tr.appendChild(el('td'));
    const button = (label, fn, cls) => {
      const b = el('button', 'btn btn-small ' + (cls || 'btn-ghost'), label);
      b.type = 'button';
      b.addEventListener('click', fn);
      actions.appendChild(b);
    };
    button('Log', () => hpcLog(j));
    if (j.kind === 'job') {
      button(j.fetched ? 'Re-fetch' : 'Fetch', () => hpcFetch(j.name), j.state === 'DONE' && !j.fetched ? 'btn-primary' : 'btn-ghost');
      if (j.fetched) button('Results', () => hpcShowResults(j.name));
    }
    if (j.state === 'PENDING' || j.state === 'RUNNING') {
      button('Cancel', () => hpcCancel(j.name), 'btn-danger');
    }
  });
}

async function hpcRefresh(quiet) {
  try {
    const r = await hapi('refresh', {});
    H.jobs = r.jobs || [];
  } catch (err) {
    if (!quiet) hpcError('Status refresh failed: ' + err.message);
    try { H.jobs = (await hapi('jobs')).jobs || []; } catch (e) { /* keep */ }
  }
  renderJobs();
  const live = H.jobs.some((j) => j.state === 'PENDING' || j.state === 'RUNNING');
  clearTimeout(H.timer);
  if (live && !$('view-hpc').classList.contains('hidden')) {
    H.timer = setTimeout(() => hpcRefresh(true), 30000);
  }
}

async function hpcLog(job) {
  const box = $('h-log');
  box.classList.remove('hidden');
  box.textContent = 'loading…';
  try {
    const r = await hapi('log', { name: job.name, task: numberOrNull($('h-log-task').value) || 0 });
    box.textContent = r.text;
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    box.textContent = err.message;
  }
}

async function hpcCancel(name) {
  if (!window.confirm(`Cancel ${name}? Running tasks are killed; finished results stay.`)) return;
  try {
    await hapi('cancel', { name });
  } catch (err) {
    hpcError('Cancel failed: ' + err.message);
  }
  hpcRefresh(true);
}

async function hpcFetch(name) {
  hpcError(null);
  $('h-results-panel').classList.remove('hidden');
  $('h-results-title').textContent = `Results — fetching ${name}…`;
  try {
    const r = await hapi('fetch', { name });
    renderHpcResults(r);
    hpcRefresh(true);
  } catch (err) {
    $('h-results-title').textContent = 'Results';
    hpcError('Fetch failed: ' + err.message);
  }
}

async function hpcShowResults(name) {
  try {
    renderHpcResults(await hapi('results', { name }));
  } catch (err) {
    hpcError(err.message);
  }
}

/* --------------------------------------------------------------- results */
function fmtNum(v, digits) {
  if (v === null || v === undefined || v === '') return '—';
  return typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(digits ?? 3)) : String(v);
}

function fmtPct(v) {
  return v === null || v === undefined ? '—' : (100 * v).toFixed(v < 0.01 && v > 0 ? 3 : 1) + '%';
}

function fmtSec(v) {
  if (v === null || v === undefined) return '—';
  return v < 1 ? `${(v * 1000).toFixed(0)} ms` : v < 120 ? `${v.toFixed(1)} s`
    : v < 7200 ? `${(v / 60).toFixed(1)} min` : `${(v / 3600).toFixed(1)} h`;
}

function renderHpcResults(r) {
  H.results = r;
  $('h-results-panel').classList.remove('hidden');
  $('h-results-title').textContent = `Results — ${r.name}`;
  $('h-results-dir').textContent = r.dir;
  const rows = r.rows || [];
  const tasks = new Set(rows.map((x) => x.task));
  const failed = rows.filter((x) => x.status !== 'done' || x.error).length;
  const classical = rows.filter((x) => x.kind === 'classical' && x.best_objective !== null && x.best_objective !== undefined);
  const quantum = rows.filter((x) => (x.kind === 'anneal' || x.kind === 'qaoa') && !x.error);
  const hits = quantum.filter((x) => x.ip_objective !== null && x.best_objective !== null
    && x.best_objective <= x.ip_objective + 1e-9).length;
  const stats = $('h-results-stats');
  stats.textContent = '';
  stats.append(
    statTile('Tasks', String(tasks.size), `${failed} with errors`),
    statTile('IP solved', String(classical.length),
             classical.length ? `${classical.filter((x) => x.proven_optimal).length} proven optimal` : ''),
    statTile('Quantum runs', String(quantum.length),
             quantum.length ? `${hits} reached the IP optimum` : ''),
  );
  const table = $('h-results');
  table.textContent = '';
  const head = el('tr');
  ['Task', 'Test case / QUBO', 'Kind', 'Method', 'IP opt.', 'Best', 'Feasible', 'P(opt)', 'r',
   'Time', 'Node', ''].forEach((h) => head.appendChild(el('th', null, h)));
  table.appendChild(el('thead')).appendChild(head);
  const body = table.appendChild(el('tbody'));
  rows.forEach((x) => {
    const tr = body.appendChild(el('tr'));
    tr.appendChild(el('td', null, x.task));
    tr.appendChild(el('td', null, x.instance || (x.qubo || '').split('/').pop() || '—'));
    tr.appendChild(el('td', null, x.kind || '—'));
    tr.appendChild(el('td', null, x.error ? `${x.method || ''} — ${x.error}` : (x.method || '—')));
    tr.appendChild(el('td', 'num', fmtNum(x.kind === 'classical' ? x.best_objective : x.ip_objective, 0)));
    tr.appendChild(el('td', 'num', fmtNum(x.best_objective, 0)));
    tr.appendChild(el('td', 'num', fmtPct(x.feasible_fraction)));
    tr.appendChild(el('td', 'num', fmtPct(x.optimum_fraction ?? x.p_optimum_exact)));
    tr.appendChild(el('td', 'num', fmtNum(x.approximation_ratio, 3)));
    tr.appendChild(el('td', 'num', fmtSec(x.seconds ?? x.task_seconds)));
    tr.appendChild(el('td', null, x.node || '—'));
    const actions = tr.appendChild(el('td'));
    const button = (label, fn) => {
      const b = el('button', 'btn btn-small btn-ghost', label);
      b.type = 'button';
      b.addEventListener('click', fn);
      actions.appendChild(b);
    };
    if (x.kind === 'classical' && x.best_objective !== null && x.best_objective !== undefined) {
      button('Solution', () => hpcShowClassical(r.name, x.task));
      if (x.qubo_export) button('Load QUBO', () => hpcImportQubo(r.name, 'results/' + x.qubo_export.replace(/^results\//, '')));
    }
    if ((x.kind === 'anneal' || x.kind === 'qaoa') && !x.error) {
      button('Plan', () => hpcQuantumAction(r.name, x, 'plan'));
      button('Compare', () => hpcQuantumAction(r.name, x, 'compare'));
    }
  });
}

async function hpcShowClassical(name, taskId) {
  try {
    const rec = await hapi('task', { name, id: taskId });
    const data = rec.result && rec.result.classical;
    if (!data) throw new Error('no classical result in this task');
    stopPlayback();
    state.result = data;
    state.classicalResult = data;
    state.steps = data.steps || [];
    updateClassicalBadge();
    setView('classical');
    $('empty-state').classList.add('hidden');
    $('error-box').classList.add('hidden');
    renderResult();
  } catch (err) {
    hpcError(err.message);
  }
}

async function hpcImportQubo(name, path) {
  try {
    const r = await hapi('import-qubo', { name, path });
    await loadQubo(r.qubo);
  } catch (err) {
    hpcError('Could not load the QUBO: ' + err.message);
  }
}

async function hpcQuantumAction(name, row, what) {
  try {
    const rec = await hapi('task', { name, id: row.task });
    const list = (rec.result && rec.result[row.kind]) || [];
    const run = list[row.sub || 0];
    if (!run || !run.scores) throw new Error('no scored samples in this result');
    const label = row.kind === 'anneal' ? `${run.methodLabel} on FRIDA` : `QAOA p=${run.reps} simulated on FRIDA`;
    if (what === 'plan') {
      if (!run.scores.plan) throw new Error('no sample of this run replays into a valid plan');
      showPlan(run.scores.plan, label);
      return;
    }
    const wanted = (row.qubo || '').split('/').pop().replace(/^hpc-/, '');
    const loaded = qubo.model ? qubo.model.path.split('/').pop().replace(/^hpc-/, '') : null;
    if (!loaded || loaded !== wanted) {
      throw new Error(`load ${wanted} in the Quantum tab first (the comparison is per QUBO)`);
    }
    recordRun({
      id: `hpc-${name}-${row.task}-${row.kind}-${row.sub || 0}`,
      kind: row.kind, hardware: false, label,
      platform: `FRIDA ${rec.slurm && rec.slurm.host ? rec.slurm.host : ''} (classical simulation)`,
      scores: run.scores, seconds: run.wallTime ?? run.optimiseSeconds,
      note: row.kind === 'qaoa' ? `r = ${fmtNum(run.approximationRatio, 3)}, ${run.simulator || ''}` : `${run.reads} reads`,
      qpu: '—',
    });
    setView('quantum');
    setSub('summary');
  } catch (err) {
    hpcError(err.message);
  }
}

/* ------------------------------------------------------------------ init */
function onHpcShown() {
  if (!H.info) return;
  fillNoise();
  fillInstances();
  updateQuboName();
  hpcRefresh(true);
}

async function initHpc() {
  try {
    H.info = await hapi('info');
  } catch (err) {
    $('h-conn-note').textContent = 'HPC back end unavailable: ' + err.message;
    return;
  }
  fillConfig(H.info.config);
  fillChoices();
  H.jobs = H.info.jobs || [];
  renderJobs();
  setMode('classical');
  if (H.info.config.resolvedBase) {
    $('hpc-badge').textContent = H.info.config.transport === 'local' ? 'local' : 'configured';
  }
}

document.querySelectorAll('#h-mode .seg-btn').forEach((b) =>
  b.addEventListener('click', () => setMode(b.dataset.mode)));
document.querySelectorAll('#h-preview-tabs .seg-btn').forEach((b) =>
  b.addEventListener('click', () => showPreviewFile(b.dataset.file)));
$('h-check').addEventListener('click', hpcCheck);
$('h-sync').addEventListener('click', hpcSync);
$('h-image-cpu').addEventListener('click', () => hpcBuildImage('cpu'));
$('h-image-gpu').addEventListener('click', () => hpcBuildImage('gpu'));
$('h-preview').addEventListener('click', hpcPreview);
$('h-submit').addEventListener('click', hpcSubmit);
$('h-refresh').addEventListener('click', () => hpcRefresh(false));
$('h-filter').addEventListener('input', fillInstances);
$('h-instances').addEventListener('change', countInstances);
$('h-select-shown').addEventListener('click', () => {
  [...$('h-instances').options].forEach((o) => { o.selected = true; });
  countInstances();
});
$('h-select-none').addEventListener('click', () => {
  [...$('h-instances').options].forEach((o) => { o.selected = false; });
  countInstances();
});
$('h-select-current').addEventListener('click', () => {
  const name = $('instance-select').value;
  [...$('h-instances').options].forEach((o) => { o.selected = o.value === name; });
  countInstances();
});
$('h-partition').addEventListener('change', partitionChanged);
['h-gpu', 'h-backend', 'h-mem'].forEach((id) => $(id).addEventListener('change', resourceNote));
$('h-gpu').addEventListener('change', () => {
  if ($('h-gpu').value && $('h-backend').value === 'aer') $('h-backend').value = 'cupy';
  if (!$('h-gpu').value && $('h-backend').value === 'cupy') $('h-backend').value = 'aer';
  resourceNote();
});

initHpc();
