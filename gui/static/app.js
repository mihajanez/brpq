/* Restricted BRP solver GUI.
   Blocks are shaded by retrieval order with the blue sequential ramp; the block
   in motion takes the orange accent and the current target a red outline. */
'use strict';

const RAMP_LIGHT = ['#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6',
                    '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'];
const RAMP_DARK  = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5',
                    '#5598e7', '#6da7ec', '#86b6ef', '#9ec5f4', '#b7d3f6'];
const DARK_INK_FROM = 6;   // ramp index at which the fill is light enough for dark text

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const state = {
  instances: [],
  instance: null,     // currently selected or designed instance
  mode: 'benchmark',  // 'benchmark' = a file from data/, 'custom' = the designer
  result: null,
  steps: [],
  stepIndex: 0,
  playing: false,
  timer: null,
  runId: null,       // id the server knows this run by, so it can be killed
  running: false,
  elapsedTimer: null,
};

/* ------------------------------------------------------------------ theme */
function isDark() {
  const set = document.documentElement.dataset.theme;
  if (set === 'dark') return true;
  if (set === 'light') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}
function ramp() { return isDark() ? RAMP_DARK : RAMP_LIGHT; }

function blockStyle(label, order, total) {
  // order: 0 = retrieved first (most urgent) -> darkest step of the ramp.
  const steps = ramp();
  const t = total > 1 ? order / (total - 1) : 0;
  const idx = isDark()
    ? Math.round(t * (steps.length - 1))               // dark mode: urgent = darkest allowed
    : Math.round((1 - t) * (steps.length - 1));
  const fill = steps[idx];
  const lightFill = isDark() ? idx >= DARK_INK_FROM : idx <= 3;
  return { fill, ink: lightFill ? '#0b0b0b' : '#ffffff' };
}

$('theme-select').value = document.documentElement.dataset.theme || 'system';
$('theme-select').addEventListener('change', (e) => {
  if (e.target.value === 'system') {
    delete document.documentElement.dataset.theme;      // back to the OS setting
  } else {
    document.documentElement.dataset.theme = e.target.value;
  }
  renderCurrent();
  renderPreview();
  renderLegend();
});
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if ($('theme-select').value === 'system') { renderCurrent(); renderPreview(); renderLegend(); }
});

/* ------------------------------------------------------------- bay drawing */
function bayGeometry(host, stacks, tiers) {
  const width = Math.max(host.clientWidth || 640, 320);
  const padLeft = 26, padRight = 10;
  const cw = Math.max(30, Math.min(62, Math.floor((width - padLeft - padRight) / stacks) - 6));
  const ch = Math.max(22, Math.min(34, Math.round(cw * 0.62)));
  const gap = 6;
  const stepX = cw + gap;
  return { cw, ch, gap, stepX, padLeft, padTop: 18, padBottom: 22,
           width: padLeft + stacks * stepX + padRight,
           height: 18 + tiers * (ch + 2) + 22 };
}

/* A block is *blocking* when some block below it in the same stack has a smaller
   priority: it has to be relocated before that one can be retrieved. The number
   of blocking blocks is the classic lower bound on the relocation count. */
function blockingSet(bay) {
  const blocking = new Set();
  bay.forEach((stack) => {
    let lowest = Infinity;                       // smallest priority below the cursor
    stack.forEach((label) => {
      if (label > lowest) blocking.add(label);
      lowest = Math.min(lowest, label);
    });
  });
  return blocking;
}

/* Renders one bay state into `host`. Block <div>s are keyed by label so that
   moving between states animates via the CSS transform transition. */
function drawBay(host, opts) {
  const { bay, order, total, tiers, target, moving, limit } = opts;
  const blocking = blockingSet(bay);
  const stacks = bay.length;
  const geo = bayGeometry(host, stacks, Math.max(tiers, ...bay.map(s => s.length), 1));
  const rows = Math.max(tiers, ...bay.map(s => s.length), 1);

  let root = host.querySelector('.bay');
  const fresh = !root || Number(root.dataset.stacks) !== stacks || Number(root.dataset.rows) !== rows;
  if (fresh) {
    host.textContent = '';
    root = el('div', 'bay');
    root.dataset.stacks = String(stacks);
    root.dataset.rows = String(rows);
    host.appendChild(root);
  }
  root.style.width = geo.width + 'px';
  root.style.height = geo.height + 'px';

  const yOf = (tier) => geo.padTop + (rows - 1 - tier) * (geo.ch + 2);
  const xOf = (stack) => geo.padLeft + stack * geo.stepX;

  if (fresh) {
    for (let s = 0; s < stacks; s++) {
      for (let t = 0; t < rows; t++) {
        const slot = el('div', 'bay-slot');
        slot.style.cssText =
          `left:${xOf(s)}px;top:${yOf(t)}px;width:${geo.cw}px;height:${geo.ch}px`;
        root.appendChild(slot);
      }
      const axis = el('div', 'bay-axis', String(s + 1));
      axis.dataset.stack = String(s + 1);
      axis.style.cssText =
        `left:${xOf(s)}px;top:${geo.padTop + rows * (geo.ch + 2) + 4}px;width:${geo.cw}px`;
      root.appendChild(axis);
    }
    for (let t = 0; t < rows; t++) {
      const tick = el('div', 'bay-tier', String(t + 1));
      tick.style.cssText = `left:0;top:${yOf(t) + geo.ch / 2 - 7}px;width:20px`;
      root.appendChild(tick);
    }
    const floor = el('div', 'bay-floor');
    floor.style.cssText =
      `left:${geo.padLeft - 4}px;top:${geo.padTop + rows * (geo.ch + 2)}px;` +
      `width:${stacks * geo.stepX - geo.gap + 8}px`;
    root.appendChild(floor);
  }

  // height limit marker
  let limitLine = root.querySelector('.bay-limit');
  if (limit && limit <= rows) {
    if (!limitLine) {
      limitLine = el('div', 'bay-limit');
      limitLine.appendChild(el('span', null, ''));
      root.appendChild(limitLine);
    }
    limitLine.style.cssText =
      `left:${geo.padLeft - 4}px;top:${yOf(limit - 1) - 3}px;` +
      `width:${stacks * geo.stepX - geo.gap + 8}px`;
    limitLine.firstChild.textContent = `height limit ${limit}`;
  } else if (limitLine) {
    limitLine.remove();
  }

  // blocks
  const seen = new Set();
  bay.forEach((stack, s) => {
    stack.forEach((label, t) => {
      seen.add(label);
      let node = root.querySelector(`[data-block="${label}"]`);
      if (!node) {
        node = el('div', 'bay-block', String(label));
        node.dataset.block = String(label);
        node.style.width = geo.cw + 'px';
        node.style.height = geo.ch + 'px';
        node.style.left = '0px';
        node.style.top = '0px';
        node.style.transform = `translate(${xOf(s)}px, ${yOf(t)}px)`;
        root.appendChild(node);
        node.getBoundingClientRect();                      // settle before animating
      }
      const skin = blockStyle(label, order.get(label), total);
      node.style.background = skin.fill;
      node.style.color = skin.ink;
      node.style.width = geo.cw + 'px';
      node.style.height = geo.ch + 'px';
      node.style.transform = `translate(${xOf(s)}px, ${yOf(t)}px)`;
      node.classList.toggle('is-target', label === target);
      node.classList.toggle('is-moving', label === moving);
      node.classList.toggle('is-blocking', blocking.has(label));
      node.classList.remove('is-leaving');
      node.title = `Block ${label} — retrieval position ${order.get(label) + 1} of ${total}` +
        (blocking.has(label) ? ' — blocking, must be relocated' : '');
    });
  });
  root.querySelectorAll('.bay-block').forEach((node) => {
    const label = Number(node.dataset.block);
    if (!seen.has(label) && !node.classList.contains('is-leaving')) {
      node.classList.add('is-leaving');
      node.classList.remove('is-target', 'is-moving', 'is-blocking');
      setTimeout(() => node.remove(), 260);
    }
  });
  root.querySelectorAll('.bay-axis').forEach((node) => {
    node.classList.toggle('is-target-stack',
      opts.targetStack !== null && Number(node.dataset.stack) === opts.targetStack + 1);
  });
}

function orderMap(instance) {
  const labels = [].concat(...instance.bay).sort((a, b) => a - b);
  const map = new Map();
  labels.forEach((label, i) => map.set(label, i));
  return { order: map, total: labels.length };
}

/* -------------------------------------------------------------- instances */
async function loadInstances(opts) {
  const res = await fetch('/api/instances');
  const data = await res.json();
  state.instances = data.instances || [];
  const pill = $('solver-status');
  if (data.solverAvailable) {
    pill.className = 'pill pill-good';
    pill.textContent = 'solver ready (rbrp_ip)';
  } else {
    pill.className = 'pill pill-bad';
    pill.textContent = 'rbrp_ip missing — run make';
    $('run-button').disabled = true;
  }
  fillInstanceList();
  if (opts && opts.keepSelection) return;
  const preferred = state.instances.find((i) => i.name === 'data03-06-29.dat')
    || state.instances[0];
  if (preferred) {
    $('instance-select').value = preferred.name;
    selectInstance(preferred.name);
  }
}

function fillInstanceList() {
  const filter = $('instance-filter').value.trim().toLowerCase();
  const select = $('instance-select');
  const keep = select.value;
  select.textContent = '';
  state.instances
    .filter((i) => !filter || i.name.toLowerCase().includes(filter))
    .forEach((i) => {
      const option = el('option', null,
        `${i.name.replace(/\.dat$|\.txt$/, '')}   ${i.stacks ?? '?'}×${i.fileTiers ?? '?'}  ${i.blocks ?? '?'} blocks`);
      option.value = i.name;
      select.appendChild(option);
    });
  if ([...select.options].some((o) => o.value === keep)) select.value = keep;
}

async function selectInstance(name) {
  const res = await fetch('/api/instance?name=' + encodeURIComponent(name));
  const data = await res.json();
  if (data.error) { showError(data.error); return; }
  state.instance = data;
  if (state.result && state.result.instance.name !== name) {
    stopPlayback();                       // a stale plan belongs to another instance
    state.result = null;
    state.steps = [];
    $('result-view').classList.add('hidden');
    $('empty-state').classList.remove('hidden');
    $('error-box').classList.add('hidden');
  }
  renderFacts();
  renderPreview();
  updateCommandPreview();
}

function heightLimit() {
  const inst = state.instance;
  if (!inst) return null;
  const empty = numberOrNull($('p-empty-tiers').value);
  const maxT = numberOrNull($('p-max-height').value);
  let tiers = 0;
  if (empty !== null && empty >= 0) tiers = Math.min(inst.fileTiers + empty, inst.blocks);
  if (maxT) {
    tiers = tiers === 0 ? Math.min(maxT, inst.blocks) : Math.max(maxT, tiers);
    tiers = Math.max(tiers, inst.maxStackHeight);
  }
  return tiers === 0 ? inst.blocks : tiers;
}

function renderFacts() {
  const inst = state.instance;
  const facts = $('instance-facts');
  facts.textContent = '';
  const limit = heightLimit();
  const rows = [
    ['Stacks', inst.stacks],
    ['Tiers in file', inst.fileTiers],
    ['Blocks', inst.blocks],
    ['Fill rate', `${Math.round(100 * inst.blocks / (inst.stacks * inst.fileTiers))}%`],
    ['Blocking blocks', blockingSet(inst.bay).size],
    ['Height limit', limit >= inst.blocks ? `${limit} (unlimited)` : limit],
  ];
  rows.forEach(([k, v]) => {
    facts.appendChild(el('dt', null, k));
    facts.appendChild(el('dd', null, String(v)));
  });
}

function renderPreview() {
  if (!state.instance || !state.instance.blocks || state.mode === 'custom') return;
  const { order, total } = orderMap(state.instance);
  const target = Math.min(...[].concat(...state.instance.bay));
  drawBay($('preview-bay'), {
    bay: state.instance.bay, order, total,
    tiers: Math.min(heightLimit(), Math.max(state.instance.maxStackHeight + 2, 3)),
    target, moving: null, limit: heightLimit(),
    targetStack: state.instance.bay.findIndex((s) => s.includes(target)),
  });
}

/* --------------------------------------------------------------- designer */
const designer = { stacks: 4, tiers: 4, cells: [] };   // cells[stack][tier] = '' | '7'

function designerResize(stacks, tiers) {
  designer.stacks = Math.max(1, Math.min(20, stacks || 1));
  designer.tiers = Math.max(1, Math.min(20, tiers || 1));
  const cells = [];
  for (let s = 0; s < designer.stacks; s++) {
    const column = [];
    for (let t = 0; t < designer.tiers; t++) {
      column.push((designer.cells[s] && designer.cells[s][t]) || '');
    }
    cells.push(column);
  }
  designer.cells = cells;
}

/* Blocks settle to the bottom of their stack, so a gap left while typing is not
   an error — it just closes. */
function designerBay() {
  return designer.cells.map((column) =>
    column.filter((v) => v !== '').map(Number));
}

function designerGravity() {
  designer.cells = designer.cells.map((column) => {
    const values = column.filter((v) => v !== '');
    while (values.length < designer.tiers) values.push('');
    return values;
  });
}

function designerInstance() {
  const bay = designerBay();
  return {
    name: ($('d-name').value.trim() || 'custom instance'),
    stacks: designer.stacks,
    fileTiers: designer.tiers,
    tiers: designer.tiers,
    blocks: bay.reduce((n, stack) => n + stack.length, 0),
    bay,
    maxStackHeight: Math.max(0, ...bay.map((s) => s.length)),
  };
}

function designerProblems() {
  const bay = designerBay();
  const labels = [].concat(...bay);
  const problems = [];
  if (!labels.length) problems.push('place at least one block');
  const counts = new Map();
  labels.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
  const duplicates = [...counts.entries()].filter(([, c]) => c > 1).map(([v]) => v);
  if (duplicates.length) {
    problems.push('duplicate priorities: ' + duplicates.sort((a, b) => a - b).join(', '));
  }
  if (labels.some((v) => !Number.isInteger(v) || v < 1)) {
    problems.push('priorities must be whole numbers of 1 or more');
  }
  return { problems, duplicates: new Set(duplicates) };
}

function buildDesignerGrid() {
  designerResize(designer.stacks, designer.tiers);   // make sure cells match the DOM
  const host = $('designer-grid');
  host.textContent = '';
  host.style.gridTemplateColumns = `22px repeat(${designer.stacks}, 46px)`;
  for (let t = designer.tiers - 1; t >= 0; t--) {
    host.appendChild(el('div', 'designer-axis tier', String(t + 1)));
    for (let s = 0; s < designer.stacks; s++) {
      const cell = el('input', 'designer-cell');
      cell.type = 'text';
      cell.inputMode = 'numeric';
      cell.maxLength = 3;
      cell.dataset.stack = String(s);
      cell.dataset.tier = String(t);
      cell.title = `stack ${s + 1}, tier ${t + 1}`;
      cell.addEventListener('input', () => {
        cell.value = cell.value.replace(/[^0-9]/g, '');
        designer.cells[s][t] = cell.value;
        syncDesigner({ repaint: false });
      });
      cell.addEventListener('change', () => syncDesigner());
      host.appendChild(cell);
    }
  }
  host.appendChild(el('div', 'designer-axis', ''));
  for (let s = 0; s < designer.stacks; s++) {
    host.appendChild(el('div', 'designer-axis', String(s + 1)));
  }
  syncDesigner();
}

/* Repaint values and colours, and push the designed bay into the rest of the GUI. */
function syncDesigner(opts) {
  const repaint = !opts || opts.repaint !== false;
  if (repaint) designerGravity();
  const { problems, duplicates } = designerProblems();
  const bay = designerBay();
  const labels = [].concat(...bay).sort((a, b) => a - b);
  const order = new Map();
  labels.forEach((label, i) => order.set(label, i));

  $('designer-grid').querySelectorAll('.designer-cell').forEach((cell) => {
    const s = Number(cell.dataset.stack), t = Number(cell.dataset.tier);
    if (repaint) cell.value = designer.cells[s][t];
    const value = cell.value;
    cell.classList.toggle('is-empty', value === '');
    cell.classList.toggle('is-dup', value !== '' && duplicates.has(Number(value)));
    if (value === '') {
      cell.style.background = '';
      cell.style.color = '';
    } else {
      const skin = blockStyle(Number(value), order.get(Number(value)) || 0, labels.length);
      cell.style.background = skin.fill;
      cell.style.color = skin.ink;
    }
  });

  const status = $('designer-status');
  status.classList.toggle('is-bad', problems.length > 0);
  if (problems.length) {
    status.textContent = problems.join(' · ');
  } else {
    const blocking = blockingSet(bay).size;
    status.textContent = `${labels.length} blocks, ${blocking} of them blocking ` +
      `(at least ${blocking} relocations) — ready to run.`;
  }
  $('run-button').disabled = !state.running && problems.length > 0;

  state.instance = designerInstance();
  renderFacts();
  updateCommandPreview();
}

function randomDesign() {
  const n = designer.stacks * designer.tiers;
  const order = [...Array(n).keys()].map((i) => i + 1);
  for (let i = n - 1; i > 0; i--) {                 // Fisher-Yates
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  designer.cells = designer.cells.map((column, s) =>
    column.map((_, t) => String(order[s * designer.tiers + t])));
  syncDesigner();
}

/* Renumber whatever was typed to a clean 1..N, keeping the relative order. */
function renumberDesign() {
  const labels = [].concat(...designerBay()).sort((a, b) => a - b);
  const rank = new Map();
  labels.forEach((label, i) => { if (!rank.has(label)) rank.set(label, i + 1); });
  designer.cells = designer.cells.map((column) =>
    column.map((v) => (v === '' ? '' : String(rank.get(Number(v))))));
  syncDesigner();
}

function designFromInstance(inst) {
  designerResize(inst.stacks, Math.max(inst.fileTiers, inst.maxStackHeight));
  designer.cells = designer.cells.map((column, s) =>
    column.map((_, t) => {
      const stack = inst.bay[s] || [];
      return t < stack.length ? String(stack[t]) : '';
    }));
  $('d-stacks').value = String(designer.stacks);
  $('d-tiers').value = String(designer.tiers);
  buildDesignerGrid();
}

async function saveDesign() {
  const { problems } = designerProblems();
  if (problems.length) return;
  const note = $('d-save-note');
  note.textContent = 'saving…';
  try {
    const res = await fetch('/api/save-instance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: $('d-name').value.trim() || 'custom',
                             bay: designerBay(), tiers: designer.tiers }),
    });
    const data = await res.json();
    if (data.error) { note.textContent = data.error; return; }
    note.textContent = (data.overwritten ? 'replaced ' : 'saved as ') + data.name +
      ' — it is now in the Benchmark file list';
    await loadInstances({ keepSelection: true });
  } catch (err) {
    note.textContent = String(err);
  }
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('#mode-switch .seg-btn').forEach((btn) =>
    btn.classList.toggle('is-active', btn.dataset.mode === mode));
  $('benchmark-picker').classList.toggle('hidden', mode === 'custom');
  $('custom-hint').classList.toggle('hidden', mode !== 'custom');
  $('designer').classList.toggle('hidden', mode !== 'custom');
  $('empty-state').classList.toggle('hidden', mode === 'custom' || !!state.result);
  $('error-box').classList.add('hidden');
  if (mode === 'custom') {
    $('result-view').classList.add('hidden');
    stopPlayback();
    state.result = null;
    if (!$('designer-grid').children.length) buildDesignerGrid(); else syncDesigner();
  } else {
    $('run-button').disabled = false;
    const name = $('instance-select').value;
    if (name) selectInstance(name);
  }
}

/* ------------------------------------------------------------- parameters */
function numberOrNull(value) {
  if (value === '' || value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function currentOptions() {
  const target = state.mode === 'custom'
    ? { bay: designerBay(), tiers: designer.tiers,
        name: $('d-name').value.trim() || 'custom instance' }
    : { instance: $('instance-select').value };
  return {
    ...target,
    emptyTiers: numberOrNull($('p-empty-tiers').value),
    maximumHeight: numberOrNull($('p-max-height').value),
    timeLimit: numberOrNull($('p-time-limit').value),
    threads: numberOrNull($('p-threads').value),
    threshold: numberOrNull($('p-threshold').value),
    disableGreedy: $('p-disable-greedy').checked,
    disableUpperBound: $('p-disable-ub').checked,
    verbose: $('p-verbose').checked ? 1 : null,
    exportQubo: $('p-export-qubo').checked ? 'problem.qubo' : null,
  };
}

function updateCommandPreview() {
  const o = currentOptions();
  const parts = ['./rbrp_ip'];
  if (o.verbose) parts.push('-v', String(o.verbose));
  if (o.emptyTiers !== null && o.emptyTiers >= 0) parts.push('-E', String(o.emptyTiers));
  if (o.maximumHeight) parts.push('-T', String(o.maximumHeight));
  if (o.timeLimit) parts.push('-t', String(o.timeLimit));
  if (o.threads) parts.push('-m', String(o.threads));
  if (o.threshold !== null) parts.push('-s', String(o.threshold));
  if (o.disableGreedy) parts.push('-g');
  if (o.disableUpperBound) parts.push('-u');
  if (o.exportQubo) parts.push('-Q', o.exportQubo);
  parts.push(state.mode === 'custom' ? `<${o.name}>` : 'data/' + (o.instance || '<instance>'));
  $('command-preview').textContent = parts.join(' ');
  if (state.instance) renderFacts();
  renderPreview();
}

/* ------------------------------------------------------------------- solve */
async function runSolver() {
  const options = currentOptions();
  if (state.mode === 'custom') {
    const { problems } = designerProblems();
    if (problems.length) { showError('Finish the design first: ' + problems.join('; ')); return; }
  } else if (!options.instance) {
    showError('Select a test case first.');
    return;
  }
  stopPlayback();
  $('error-box').classList.add('hidden');
  $('empty-state').classList.add('hidden');
  $('result-view').classList.add('hidden');
  $('busy').classList.remove('hidden');
  $('busy-title').textContent = `Solving ${options.instance || options.name}…`;
  $('busy-note').textContent = options.timeLimit
    ? `Time limit ${options.timeLimit}s.`
    : 'No time limit set — large instances can take a while.';

  state.runId = (crypto.randomUUID && crypto.randomUUID()) ||
    `run-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  setRunning(true);

  try {
    const res = await fetch('/api/solve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...options, runId: state.runId }),
    });
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    state.result = data;
    state.steps = data.steps || [];
    if (data.stopped) {
      showStopNotice(data);
      if (!data.relocations.length) { state.result = null; return; }
    }
    renderResult();
  } catch (err) {
    showError(String(err));
  } finally {
    setRunning(false);
    $('busy').classList.add('hidden');
    // a run with "Also export QUBO" may have just written a new file
    if (typeof loadQuboFiles === 'function') loadQuboFiles();
  }
}

/* While a solve is in flight the Run button becomes the Stop button, so there is
   always exactly one obvious action. */
function setRunning(running) {
  state.running = running;
  const button = $('run-button');
  button.textContent = running ? 'Stop solver' : 'Run solver';
  button.classList.toggle('btn-danger', running);
  button.classList.toggle('btn-primary', !running);
  button.disabled = false;
  $('busy-stop').disabled = false;
  $('busy-stop').textContent = 'Stop solver';
  clearInterval(state.elapsedTimer);
  if (running) {
    const started = Date.now();
    const base = $('busy-note').textContent;
    const tick = () => {
      const seconds = (Date.now() - started) / 1000;
      $('busy-note').textContent = base + ` — running for ${seconds.toFixed(1)}s`;
    };
    state.elapsedTimer = setInterval(tick, 100);
    tick();
  } else {
    state.elapsedTimer = null;
    state.runId = null;
  }
}

async function stopSolver() {
  if (!state.running || !state.runId) return;
  $('run-button').disabled = true;
  $('busy-stop').disabled = true;
  $('busy-stop').textContent = 'stopping…';
  $('busy-title').textContent = 'Stopping the solver…';
  try {
    await fetch('/api/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ runId: state.runId }),
    });
  } catch (err) {
    showError('Could not stop the solver: ' + err);
  }
}

/* A stopped run keeps whatever bounds the solver had already printed. */
function showStopNotice(r) {
  const s = r.stats || {};
  const bits = [];
  if (s.greedy_upper_bound !== undefined) bits.push(`greedy upper bound ${s.greedy_upper_bound}`);
  if (s.lower_bound !== undefined) bits.push(`lower bound ${s.lower_bound}`);
  if (s.iterations !== undefined) bits.push(`${s.iterations} iteration(s)`);
  const box = $('error-box');
  box.textContent = '';
  box.classList.add('notice');
  box.appendChild(el('strong', null,
    `Solving stopped after ${(r.wallTime || 0).toFixed(1)}s — the solver and everything ` +
    'it started were killed.'));
  box.appendChild(el('pre', null, bits.length
    ? 'Best figures it had reached: ' + bits.join(', ') +
      (r.relocations.length ? '' : '. No move plan was produced.')
    : 'It was stopped before reporting any bounds.'));
  box.classList.remove('hidden');
  if (!r.relocations.length) {
    $('result-view').classList.add('hidden');
    $('empty-state').classList.toggle('hidden', state.mode === 'custom');
  }
}

function showError(message, detail) {
  const box = $('error-box');
  box.classList.remove('notice');
  box.textContent = '';
  box.appendChild(el('strong', null, message));
  if (detail) box.appendChild(el('pre', null, detail));
  box.classList.remove('hidden');
  $('busy').classList.add('hidden');
  if (!state.result) $('empty-state').classList.remove('hidden');
}

/* ------------------------------------------------------------ result views */
function statTile(label, value, note, hero) {
  const tile = el('div', 'stat' + (hero ? ' stat-hero' : ''));
  tile.appendChild(el('div', 'stat-label', label));
  tile.appendChild(el('div', 'stat-value', value));
  if (note) tile.appendChild(el('div', 'stat-note', note));
  return tile;
}

function renderStats() {
  const r = state.result, s = r.stats || {};
  const row = $('stat-row');
  row.textContent = '';

  if (r.source === 'qubo') {           // a plan replayed from a QUBO selection
    const blocking = blockingSet(r.instance.bay).size;
    row.appendChild(statTile('Relocations', String(r.relocations.length),
      'from the selected QUBO columns', true));
    row.appendChild(statTile('QUBO energy',
      r.energy === undefined ? '—' : String(r.energy), 'slacks relaxed'));
    row.appendChild(statTile('Blocking blocks', String(blocking),
      `at start — lower bound ${blocking}`));
    row.appendChild(statTile('Retrievals', String(r.retrievals),
      `${r.instance.blocks} blocks cleared`));
    row.appendChild(statTile('Height limit', String(r.heightLimit),
      'applied while replaying'));
    return;
  }

  const proven = s.optimal_value !== undefined;
  const relocations = r.relocations.length;
  const gap = relocations - (s.lower_bound ?? relocations);
  row.appendChild(statTile('Relocations', String(relocations),
    proven ? 'proven optimal'
           : `best found — up to ${gap} above the bound`, true));
  row.appendChild(statTile('Lower bound',
    String(s.optimal_value !== undefined ? s.optimal_value : (s.lower_bound ?? '—')),
    proven ? 'matches the solution' : 'best proven bound'));
  row.appendChild(statTile('Greedy upper bound',
    s.greedy_upper_bound !== undefined ? String(s.greedy_upper_bound) : 'off',
    s.greedy_upper_bound !== undefined && s.greedy_upper_bound > relocations
      ? `${s.greedy_upper_bound - relocations} moves worse than optimal`
      : 'heuristic starting point'));
  row.appendChild(statTile('Solve time', `${(s.total_time ?? r.wallTime ?? 0).toFixed(2)}s`,
    `IP time ${(s.total_ip_time ?? 0).toFixed(2)}s · ${s.iterations ?? 1} iteration(s)`));
  row.appendChild(statTile('IP model',
    `${s.final_number_of_variables ?? '—'} vars`,
    `${s.final_number_of_constraints ?? '—'} constraints` +
    (s.initial_number_of_variables !== undefined && s.initial_number_of_variables !== s.final_number_of_variables
      ? ` (from ${s.initial_number_of_variables})` : '')));
  const blocking = blockingSet(r.instance.bay).size;
  row.appendChild(statTile('Blocking blocks', String(blocking),
    `at start — lower bound ${blocking}`));
  row.appendChild(statTile('Retrievals', String(r.retrievals),
    `${r.instance.blocks} blocks cleared`));
}

function renderLegend() {
  const legend = $('bay-legend');
  legend.textContent = '';
  const swatches = el('div', 'legend-swatches');
  const steps = ramp();
  const shown = isDark() ? steps : [...steps].reverse();   // left = retrieved first
  shown.forEach((hex) => { const i = el('i'); i.style.background = hex; swatches.appendChild(i); });
  const wrap = el('div', 'legend-ramp');
  wrap.appendChild(el('span', null, 'retrieved first'));
  wrap.appendChild(swatches);
  wrap.appendChild(el('span', null, 'last'));
  legend.appendChild(wrap);

  const target = el('span', 'legend-key');
  const tbox = el('span', 'box'); tbox.style.color = 'var(--target)';
  target.append(tbox, 'target block');
  const moving = el('span', 'legend-key');
  const mbox = el('span', 'box'); mbox.style.color = 'var(--moving)';
  moving.append(mbox, 'in motion');
  const blocking = el('span', 'legend-key');
  blocking.appendChild(el('span', 'box hatch'));
  blocking.append('blocking (must be relocated)');
  legend.append(target, moving, blocking);
}

function renderRelocTable() {
  const body = $('reloc-table').querySelector('tbody');
  body.textContent = '';
  const { order, total } = orderMap(state.result.instance);
  state.result.relocations.forEach((rel) => {
    const stepIndex = state.steps.findIndex(
      (s) => s.kind === 'relocate' && s.number === rel.number);
    const tr = el('tr');
    tr.dataset.step = String(stepIndex);
    tr.appendChild(el('td', null, String(rel.number)));
    const chipCell = el('td');
    const chip = el('span', 'chip', String(rel.block));
    const skin = blockStyle(rel.block, order.get(rel.block), total);
    chip.style.background = skin.fill; chip.style.color = skin.ink;
    chipCell.appendChild(chip);
    tr.appendChild(chipCell);
    tr.appendChild(el('td', null, String(rel.src)));
    tr.appendChild(el('td', null, String(rel.dst)));
    tr.appendChild(el('td', null, stepIndex >= 0 ? String(stepIndex) : '—'));
    tr.addEventListener('click', () => { stopPlayback(); goToStep(stepIndex); });
    body.appendChild(tr);
  });
  const perStack = {};
  state.result.relocations.forEach((r) => {
    perStack[r.dst] = (perStack[r.dst] || 0) + 1;
  });
  const busiest = Object.entries(perStack).sort((a, b) => b[1] - a[1])[0];
  $('reloc-summary').textContent = busiest
    ? `${state.result.relocations.length} moves · stack ${busiest[0]} receives most (${busiest[1]})`
    : `${state.result.relocations.length} moves`;
}

function renderInterpretation() {
  const r = state.result, s = r.stats || {};
  const box = $('interpretation');
  box.textContent = '';

  if (r.source === 'qubo') {
    const blocking = blockingSet(r.instance.bay).size;
    const n = r.relocations.length;
    const p = el('p');
    p.innerHTML = `This plan is the selection of QUBO columns replayed move by move: ` +
      `<strong>${n}</strong> relocations to clear all <strong>${r.instance.blocks}</strong> ` +
      'blocks. Every move here comes from the <code># reloc</code> metadata of the columns ' +
      'that were set to 1, so it is what a sampler returning that bitstring would actually mean.';
    box.appendChild(p);
    const list = el('ul');
    list.appendChild(el('li', null,
      `${blocking} blocks start out blocking, so no selection can do better than ${blocking} ` +
      `relocations; this one uses ${n}.`));
    list.appendChild(el('li', null,
      'Run the same instance through the classical solver to compare: an optimal IP result ' +
      'and the best replayable QUBO selection should agree when both use the same height limit.'));
    box.appendChild(list);
    return;
  }
  const proven = s.optimal_value !== undefined;
  const n = r.relocations.length;
  const blocks = r.instance.blocks;
  const repeats = {};
  r.relocations.forEach((rel) => { repeats[rel.block] = (repeats[rel.block] || 0) + 1; });
  const multi = Object.entries(repeats).filter(([, c]) => c > 1);

  const p1 = el('p');
  p1.innerHTML = proven
    ? `All <strong>${blocks}</strong> blocks can be retrieved in priority order with ` +
      `<strong>${n}</strong> relocation${n === 1 ? '' : 's'}, and the IP model <strong>proved` +
      `</strong> that no plan does better (lower bound = upper bound = ${s.optimal_value}).`
    : `The best plan found needs <strong>${n}</strong> relocation${n === 1 ? '' : 's'}; the ` +
      `proven lower bound is <strong>${s.lower_bound ?? '—'}</strong>, so up to ` +
      `<strong>${Math.max(0, n - (s.lower_bound ?? n))}</strong> move(s) might still be saved. ` +
      `Raise the time limit to close the gap.`;
  box.appendChild(p1);

  const list = el('ul');
  list.appendChild(el('li',
    null, `${n} relocations for ${blocks} retrievals — ${(n / blocks).toFixed(2)} moves per block.`));
  const blocking = blockingSet(r.instance.bay).size;
  list.appendChild(el('li', null,
    `${blocking} of the ${blocks} blocks start out blocking (hatched in the bay), so at least ` +
    `${blocking} relocations are unavoidable; this plan uses ${n}` +
    (n > blocking ? ` — ${n - blocking} block(s) had to be moved more than once or moved aside.`
                  : ' — every blocking block is moved exactly once.')));
  if (s.greedy_upper_bound !== undefined) {
    const delta = s.greedy_upper_bound - n;
    let note;
    if (delta > 0) {
      note = `The greedy heuristic needed ${s.greedy_upper_bound} moves; the exact model saves ${delta}.`;
    } else if (proven) {
      note = 'The greedy heuristic already found an optimal plan; the model proved it.';
    } else {
      note = `The plan shown is still the greedy heuristic's (${s.greedy_upper_bound} moves) — ` +
             'the model did not improve on it within the time limit.';
    }
    list.appendChild(el('li', null, note));
  }
  if (multi.length) {
    const shown = multi.slice(0, 8).map(([b, c]) => `${b} (${c}×)`).join(', ');
    const more = multi.length > 8 ? `, +${multi.length - 8} more` : '';
    list.appendChild(el('li', null,
      `${multi.length} block${multi.length > 1 ? 's are' : ' is'} re-handled: ` + shown + more +
      ' — moved more than once, which the height limit often forces.'));
  }
  const limit = r.heightLimit;
  const peak = Math.max(...state.steps.map((st) => Math.max(...st.bay.map((x) => x.length))));
  list.appendChild(el('li', null, limit >= blocks
    ? `No height limit was active; the plan peaked at ${peak} tiers.`
    : `Height limit ${limit} tiers; the plan peaked at ${peak}${peak === limit ? ' (tight)' : ''}.`));
  list.appendChild(el('li', null,
    `Model size: ${s.final_number_of_variables ?? '?'} variables / ` +
    `${s.final_number_of_constraints ?? '?'} constraints, solved in ` +
    `${(s.total_time ?? 0).toFixed(2)}s over ${s.iterations ?? 1} iteration(s).`));
  box.appendChild(list);

  if (r.replayError) {
    const warn = el('p');
    warn.innerHTML = `<strong>Note:</strong> the move plan could not be replayed fully — ${r.replayError}`;
    box.appendChild(warn);
  }
}

function renderSummaryTab() {
  const r = state.result, s = r.stats || {};
  const host = $('tab-summary');
  host.textContent = '';
  const dl = el('dl', 'kv');
  const rows = [
    ['command', r.command],
    ['exit code', String(r.returncode)],
    ['status', r.timedOut ? 'killed on hard timeout' : (s.solved ? 'solved' : 'not solved')],
    ['wall time', `${(r.wallTime || 0).toFixed(2)}s`],
    ['greedy_upper_bound', s.greedy_upper_bound ?? '—'],
    ['lower_bound', s.optimal_value ?? s.lower_bound ?? '—'],
    ['upper_bound', s.upperBoundInfeasible ? 'infeasible' : (s.upper_bound ?? s.optimal_value ?? '—')],
    ['lb_time / ub_time', `${s.lb_time ?? '—'} / ${s.ub_time ?? '—'}`],
    ['variables (initial → final)',
      `${s.initial_number_of_variables ?? '—'} → ${s.final_number_of_variables ?? '—'}`],
    ['constraints (initial → final)',
      `${s.initial_number_of_constraints ?? '—'} → ${s.final_number_of_constraints ?? '—'}`],
  ];
  if (r.ipVariables && r.ipVariables.length) {
    rows.push(['selected IP variables', r.ipVariables.join('  ')]);
  }
  rows.forEach(([k, v]) => {
    dl.appendChild(el('dt', null, k));
    dl.appendChild(el('dd', 'mono', String(v)));
  });
  host.appendChild(dl);
}

/* A run that produced no move plan: say why, in the GUI's own terms. */
function explainNoPlan(r) {
  const tail = (r.stderr || '').trim().split('\n').slice(-6).join('\n');
  if (/No destination found/i.test(r.stderr || '')) {
    return ['No feasible plan with a height limit of ' + r.heightLimit + ' tiers — ' +
      'there is nowhere to put a relocated block. Raise "Empty tiers" (-E) or set ' +
      '"Maximum tiers" (-T) and run again.', tail];
  }
  if (r.timedOut) {
    return [`The solver was killed after ${Math.round(r.hardTimeout)}s without returning a plan. ` +
      'Set a time limit (-t) so it reports the best bounds it has.', tail];
  }
  return ['The solver returned no relocations for this instance.', tail];
}

function renderResult() {
  const r = state.result;
  if (!r.relocations.length) {
    state.steps = [];
    $('result-view').classList.add('hidden');
    $('empty-state').classList.remove('hidden');
    renderPreview();
    showError(...explainNoPlan(r));
    return;
  }
  $('result-view').classList.remove('hidden');
  $('empty-state').classList.add('hidden');
  renderStats();
  renderLegend();
  renderRelocTable();
  renderInterpretation();
  renderSummaryTab();
  $('tab-stdout').textContent = r.stdout || '(empty)';
  $('tab-stderr').textContent = r.stderr || '(empty)';
  $('solver-output-panel').classList.toggle('hidden', r.source === 'qubo');

  const slider = $('step-slider');
  slider.max = String(Math.max(0, state.steps.length - 1));
  slider.value = '0';
  state.stepIndex = 0;
  renderCurrent();
}

function renderCurrent() {
  if (!state.result || !state.steps.length) return;
  const step = state.steps[state.stepIndex];
  const r = state.result;
  const { order, total } = orderMap(r.instance);
  const rows = Math.max(r.heightLimit >= r.instance.blocks
    ? Math.max(...state.steps.map((s) => Math.max(...s.bay.map((x) => x.length))))
    : r.heightLimit, 1);

  drawBay($('result-bay'), {
    bay: step.bay, order, total, tiers: rows,
    target: step.target, moving: step.kind === 'relocate' ? step.block : null,
    limit: r.heightLimit >= r.instance.blocks ? null : r.heightLimit,
    targetStack: step.targetStack,
  });

  const caption = $('step-caption');
  caption.textContent = '';
  if (step.kind === 'initial') {
    caption.appendChild(el('span', 'tag tag-initial', 'Start'));
    caption.appendChild(el('span', null,
      `Initial bay — ${r.instance.blocks} blocks, next to retrieve: ${step.target}`));
  } else if (step.kind === 'relocate') {
    caption.appendChild(el('span', 'tag tag-relocate', `Relocation ${step.number}`));
    caption.appendChild(el('span', null,
      `Block ${step.block} moves from stack ${step.src} to stack ${step.dst}` +
      (step.target !== null ? ` to dig out target ${step.target}` : '')));
  } else {
    caption.appendChild(el('span', 'tag tag-retrieve', `Retrieval ${step.number}`));
    caption.appendChild(el('span', null,
      `Block ${step.block} leaves the bay from stack ${step.src}` +
      (step.target !== null ? ` — next target ${step.target}` : ' — bay empty')));
  }

  const stillBlocking = blockingSet(step.bay).size;
  caption.appendChild(el('span', 'caption-note',
    `${stillBlocking} blocking block${stillBlocking === 1 ? '' : 's'} left`));

  $('step-counter').textContent = `step ${state.stepIndex} / ${state.steps.length - 1}`;
  $('step-slider').value = String(state.stepIndex);
  const currentReloc = step.kind === 'relocate' ? step.number : null;
  $('reloc-table').querySelectorAll('tbody tr').forEach((tr) => {
    tr.classList.toggle('is-current', Number(tr.dataset.step) === state.stepIndex);
    if (currentReloc && Number(tr.firstChild.textContent) === currentReloc) {
      tr.scrollIntoView({ block: 'nearest' });
    }
  });
}

/* ---------------------------------------------------------------- playback */
function goToStep(index) {
  if (!state.steps.length) return;
  state.stepIndex = Math.max(0, Math.min(state.steps.length - 1, index));
  renderCurrent();
}
function stopPlayback() {
  state.playing = false;
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
  $('btn-play').textContent = 'Play';
}
function startPlayback() {
  if (!state.steps.length) return;
  if (state.stepIndex >= state.steps.length - 1) goToStep(0);
  state.playing = true;
  $('btn-play').textContent = 'Pause';
  const tick = () => {
    if (state.stepIndex >= state.steps.length - 1) { stopPlayback(); return; }
    goToStep(state.stepIndex + 1);
  };
  state.timer = setInterval(tick, Number($('speed-select').value));
}

/* ------------------------------------------------------------------ wiring */
document.querySelectorAll('#mode-switch .seg-btn').forEach((btn) =>
  btn.addEventListener('click', () => setMode(btn.dataset.mode)));
$('d-stacks').addEventListener('change', () => {
  designerResize(Number($('d-stacks').value), designer.tiers);
  buildDesignerGrid();
});
$('d-tiers').addEventListener('change', () => {
  designerResize(designer.stacks, Number($('d-tiers').value));
  buildDesignerGrid();
});
$('d-random').addEventListener('click', randomDesign);
$('d-renumber').addEventListener('click', renumberDesign);
$('d-clear').addEventListener('click', () => {
  designer.cells = designer.cells.map((column) => column.map(() => ''));
  syncDesigner();
});
$('d-copy').addEventListener('click', () => {
  if (state.instance && state.instance.bay) designFromInstance(state.instance);
});
$('d-name').addEventListener('input', () => { state.instance = designerInstance(); updateCommandPreview(); });
$('d-save').addEventListener('click', saveDesign);
$('instance-filter').addEventListener('input', fillInstanceList);
$('instance-select').addEventListener('change', (e) => selectInstance(e.target.value));
['p-empty-tiers', 'p-max-height', 'p-time-limit', 'p-threads', 'p-threshold'].forEach((id) =>
  $(id).addEventListener('input', updateCommandPreview));
['p-disable-greedy', 'p-disable-ub', 'p-verbose', 'p-export-qubo'].forEach((id) =>
  $(id).addEventListener('change', updateCommandPreview));
$('run-button').addEventListener('click', () => (state.running ? stopSolver() : runSolver()));
$('busy-stop').addEventListener('click', stopSolver);
$('btn-play').addEventListener('click', () => (state.playing ? stopPlayback() : startPlayback()));
$('btn-next').addEventListener('click', () => { stopPlayback(); goToStep(state.stepIndex + 1); });
$('btn-prev').addEventListener('click', () => { stopPlayback(); goToStep(state.stepIndex - 1); });
$('btn-first').addEventListener('click', () => { stopPlayback(); goToStep(0); });
$('btn-last').addEventListener('click', () => { stopPlayback(); goToStep(state.steps.length - 1); });
$('step-slider').addEventListener('input', (e) => { stopPlayback(); goToStep(Number(e.target.value)); });
$('speed-select').addEventListener('change', () => { if (state.playing) { stopPlayback(); startPlayback(); } });
document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
  ['summary', 'stderr', 'stdout'].forEach((name) =>
    $('tab-' + name).classList.toggle('hidden', name !== tab.dataset.tab));
}));
document.addEventListener('keydown', (e) => {
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
  if (e.key === 'ArrowRight') { stopPlayback(); goToStep(state.stepIndex + 1); }
  else if (e.key === 'ArrowLeft') { stopPlayback(); goToStep(state.stepIndex - 1); }
  else if (e.key === ' ') { e.preventDefault(); state.playing ? stopPlayback() : startPlayback(); }
});
let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { renderPreview(); renderCurrent(); }, 150);
});

/* Deep link: /?instance=data05-08-39.dat&E=2&t=60&run=1 */
function applyQueryParams() {
  const q = new URLSearchParams(location.search);
  const map = { E: 'p-empty-tiers', T: 'p-max-height', t: 'p-time-limit',
                m: 'p-threads', s: 'p-threshold' };
  Object.entries(map).forEach(([key, id]) => {
    if (q.has(key)) $(id).value = q.get(key);
  });
  if (q.get('g')) $('p-disable-greedy').checked = true;
  if (q.get('u')) $('p-disable-ub').checked = true;
  const name = q.get('instance');
  if (name && state.instances.some((i) => i.name === name)) {
    $('instance-select').value = name;
    return selectInstance(name).then(() => q.get('run') && runSolver());
  }
  updateCommandPreview();
  if (q.get('run')) runSolver();
}

renderLegend();
loadInstances()
  .then(applyQueryParams)
  .catch((err) => showError('Could not reach the GUI server: ' + err));
