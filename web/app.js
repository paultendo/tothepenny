// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

// The page: add statements, watch each one read and reconciled, download the results. The work happens in
// worker.js; downloads are made here from bytes the worker hands back, so nothing is stored or sent anywhere.
// Rows carry properties, not sentences: a statement's reasons live in its tooltip and its peek.

const root = document.getElementById('tothepenny');
const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });

const state = { files: [], result: null, outputs: {}, urls: [], open: null, engine: 'loading', busy: false, error: '' };

const BANKS = {
  barclays: ['Barclays', '#00aeef'], hsbc: ['HSBC', '#db0011'], lloyds: ['Lloyds', '#006a4d'], halifax: ['Halifax', '#005eb8'],
  natwest: ['NatWest', '#5a287d'], santander: ['Santander', '#ec0000'], nationwide: ['Nationwide', '#1d3b8b'],
  monzo: ['Monzo', '#ff4f40'], 'metro bank': ['Metro Bank', '#e4003b'], revolut: ['Revolut', '#0666eb'], tsb: ['TSB', '#1d6fb8'],
  'crédit agricole': ['Crédit Agricole', '#00828c'], lcl: ['LCL', '#0d2f64'], pagseguro: ['PagSeguro', '#20b15a'],
};

const CSS = `
.ttp { --t-fg: var(--foreground, #1b2420); --t-2: var(--ink-muted, #56635b); --t-3: color-mix(in srgb, var(--t-2) 70%, transparent);
  --t-line: var(--line, rgba(14,30,50,.12)); --t-soft: color-mix(in srgb, var(--t-line) 55%, transparent);
  --t-bg: var(--background, #fff); --t-sheet: color-mix(in srgb, var(--t-fg) 2.5%, var(--t-bg)); --t-hover: color-mix(in srgb, var(--t-fg) 4%, transparent);
  --t-accent: var(--accent, #16a34a); --t-accent-dim: color-mix(in srgb, var(--t-accent) 14%, transparent);
  --t-red: var(--red, #d7443e); --t-red-dim: color-mix(in srgb, var(--t-red) 12%, transparent);
  --t-amber: var(--amber, #b4740f); --t-amber-dim: color-mix(in srgb, var(--t-amber) 14%, transparent);
  font: 13.5px/1.5 "Inter", ui-sans-serif, system-ui, sans-serif; font-optical-sizing: auto; font-feature-settings: "cv11", "ss03";
  letter-spacing: -.005em; color: var(--t-fg); -webkit-font-smoothing: antialiased; }
.ttp *, .ttp *::before, .ttp *::after { box-sizing: border-box; }
.ttp button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; }
.ttp .sheet { position: relative; background: var(--t-sheet); border: 1px solid var(--t-line); border-radius: 10px; container-type: inline-size;
  overflow: hidden; }
.ttp .head { display: flex; align-items: center; gap: 12px; min-height: 52px; padding: 0 14px 0 18px; border-bottom: 1px solid var(--t-line); }
.ttp .head h2 { font-size: 14px; font-weight: 600; margin: 0; letter-spacing: -.01em; display: flex; align-items: center; gap: 8px; }
.ttp .head h2 .n { font-weight: 500; color: var(--t-3); font-variant-numeric: tabular-nums; }
.ttp .head .sp { flex: 1; }
.ttp .engine { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--t-3); white-space: nowrap; }
.ttp .engine i { width: 6px; height: 6px; border-radius: 50%; background: var(--t-3); }
.ttp .engine.ready i { background: var(--t-accent); }
.ttp .engine.loading i { animation: ttp-pulse 1.2s ease-in-out infinite; }
.ttp .btn { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 11px; border-radius: 6px; font-size: 13px; font-weight: 500;
  border: 1px solid var(--t-line); background: var(--t-bg); color: var(--t-fg); text-decoration: none; white-space: nowrap;
  box-shadow: 0 1px 1px rgba(20,20,30,.04); transition: border-color .12s, background .12s; }
.ttp .btn:hover { border-color: color-mix(in srgb, var(--t-fg) 28%, transparent); }
.ttp .btn.primary { background: var(--t-accent); border-color: var(--t-accent); color: var(--t-bg); }
.ttp .btn.primary:hover { background: color-mix(in srgb, var(--t-accent) 88%, #000); }
.ttp .btn.ghost { border-color: transparent; background: transparent; box-shadow: none; color: var(--t-2); }
.ttp .btn.ghost:hover { background: var(--t-hover); }
.ttp .btn svg { flex: none; }
.ttp .menu { position: relative; }
.ttp .menu > div { position: absolute; right: 0; top: calc(100% + 6px); z-index: 5; min-width: 250px; padding: 5px; border-radius: 8px;
  background: var(--t-bg); border: 1px solid var(--t-line); box-shadow: 0 12px 40px -12px rgba(20,20,30,.35), 0 2px 6px rgba(20,20,30,.08); }
.ttp .menu a { display: flex; justify-content: space-between; gap: 16px; padding: 7px 9px; border-radius: 5px; color: var(--t-fg); text-decoration: none; font-size: 13px; }
.ttp .menu a:hover { background: var(--t-hover); }
.ttp .menu a span { color: var(--t-3); font-size: 12px; }
.ttp .empty { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 10px; padding: 64px 24px 68px; }
.ttp .empty .ic { width: 44px; height: 44px; border-radius: 11px; display: grid; place-items: center; color: var(--t-2);
  background: var(--t-hover); border: 1px solid var(--t-soft); }
.ttp .empty b { font-size: 15px; font-weight: 600; }
.ttp .empty p { margin: 0 0 6px; color: var(--t-2); max-width: 30rem; }
.ttp .stats { display: flex; flex-wrap: wrap; gap: 8px 28px; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--t-line); }
.ttp .stat { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--t-2); white-space: nowrap; }
.ttp .stat b { font-size: 15px; font-weight: 600; color: var(--t-fg); font-variant-numeric: tabular-nums; }
.ttp .stat.warn b { color: var(--t-amber); }
.ttp .group { display: flex; align-items: center; gap: 8px; height: 36px; padding: 0 18px; font-size: 12.5px; font-weight: 600; color: var(--t-2);
  background: color-mix(in srgb, var(--t-fg) 2%, transparent); border-bottom: 1px solid var(--t-soft); }
.ttp .group .n { font-weight: 500; color: var(--t-3); font-variant-numeric: tabular-nums; }
.ttp .row { display: grid; grid-template-columns: 16px minmax(0, 1fr) 8.5rem 9.5rem 11.5rem 3.5rem 6.5rem 22px; align-items: center; gap: 14px;
  height: 44px; padding: 0 12px 0 18px; border-bottom: 1px solid var(--t-soft); cursor: pointer; }
.ttp .row:hover, .ttp .row.open { background: var(--t-hover); }
.ttp .row .name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }
.ttp .row .muted { color: var(--t-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 12.5px; }
.ttp .row .mono { font-family: "Google Sans Code", ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; }
.ttp .row .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ttp .row .x { opacity: 0; width: 22px; height: 22px; border-radius: 5px; display: grid; place-items: center; color: var(--t-3); }
.ttp .row:hover .x { opacity: 1; }
.ttp .row .x:hover { background: var(--t-hover); color: var(--t-fg); }
.ttp .chip { display: inline-flex; align-items: center; gap: 6px; height: 20px; padding: 0 7px; border-radius: 5px; font-size: 12px; font-weight: 500;
  white-space: nowrap; color: var(--t-2); background: var(--t-hover); justify-self: start; }
.ttp .chip i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.ttp .chip.red { color: var(--t-red); background: var(--t-red-dim); }
.ttp .chip.amber { color: var(--t-amber); background: var(--t-amber-dim); }
.ttp .peek { padding: 14px 18px 18px 48px; border-bottom: 1px solid var(--t-soft); background: color-mix(in srgb, var(--t-fg) 1.5%, transparent); }
.ttp .props { display: grid; grid-template-columns: 8.5rem minmax(0, 1fr); gap: 7px 16px; font-size: 13px; max-width: 46rem; }
.ttp .props dt { color: var(--t-3); }
.ttp .props dd { margin: 0; font-variant-numeric: tabular-nums; }
.ttp .props dd.why { color: var(--t-fg); }
.ttp .gaprow { display: grid; grid-template-columns: 16px minmax(0, 1fr) auto; gap: 14px; align-items: center; min-height: 44px; padding: 8px 18px;
  border-bottom: 1px solid var(--t-soft); font-size: 13px; }
.ttp .gaprow .t { overflow: hidden; }
.ttp .gaprow .t b { font-weight: 500; }
.ttp .gaprow .t span { color: var(--t-2); }
.ttp .sheet > :last-child { border-bottom: 0; }
.ttp .drop { position: absolute; inset: 0; z-index: 6; display: none; place-items: center; background: color-mix(in srgb, var(--t-bg) 82%, transparent);
  border: 1.5px dashed var(--t-accent); border-radius: 10px; font-weight: 600; color: var(--t-accent); backdrop-filter: blur(2px); }
.ttp .sheet.over .drop { display: grid; }
.ttp .err { padding: 14px 18px; color: var(--t-red); border-bottom: 1px solid var(--t-soft); }
.ttp .spin { animation: ttp-spin .9s linear infinite; transform-origin: 50% 50%; }
@keyframes ttp-spin { to { transform: rotate(360deg); } }
@keyframes ttp-pulse { 50% { opacity: .35; } }
@container (max-width: 760px) {
  .ttp .row { grid-template-columns: 16px minmax(0, 1fr) 8.5rem 6.5rem 22px; }
  .ttp .row .c-account, .ttp .row .c-period, .ttp .row .c-count { display: none; }
  .ttp .peek { padding-left: 18px; }
}
@container (max-width: 560px) {
  .ttp .head { flex-wrap: wrap; row-gap: 8px; padding: 12px 14px; }
  .ttp .head h2 { flex-basis: 100%; }
  .ttp .head .sp { display: none; }
  .ttp .head .engine { margin-right: auto; }
}
@container (max-width: 480px) {
  .ttp .row { grid-template-columns: 16px minmax(0, 1fr) auto; }
  .ttp .row .x, .ttp .row.ok .c-bank, .ttp .row.bad .c-close { display: none; }
}
`;

// Icons: 16px strokes, Linear-like
const svg = (body, size = 16) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  node.setAttribute('width', size); node.setAttribute('height', size); node.setAttribute('viewBox', '0 0 16 16');
  node.setAttribute('fill', 'none'); node.setAttribute('aria-hidden', 'true');
  node.innerHTML = body; // fixed markup, never data
  return node;
};
const GLYPH = {
  done: () => svg('<circle cx="8" cy="8" r="7" fill="var(--t-accent)"/><path d="M5 8.2l2 2 4-4.4" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'),
  failed: () => svg('<circle cx="8" cy="8" r="6.3" stroke="var(--t-red)" stroke-width="1.4"/><path d="M8 4.8v3.6" stroke="var(--t-red)" stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="10.9" r=".9" fill="var(--t-red)"/>'),
  queued: () => svg('<circle cx="8" cy="8" r="6.3" stroke="var(--t-3)" stroke-width="1.4" stroke-dasharray="2.2 2.2"/>'),
  working: () => { const s = svg('<circle cx="8" cy="8" r="6.3" stroke="var(--t-line)" stroke-width="1.4"/><path d="M8 1.7a6.3 6.3 0 0 1 6.3 6.3" stroke="var(--t-accent)" stroke-width="1.6" stroke-linecap="round"/>'); s.classList.add('spin'); return s; },
  gap: () => svg('<circle cx="8" cy="8" r="6.3" stroke="var(--t-amber)" stroke-width="1.4"/><path d="M8 1.7a6.3 6.3 0 0 1 0 12.6z" fill="var(--t-amber)"/>'),
  info: () => svg('<circle cx="8" cy="8" r="6.3" stroke="var(--t-3)" stroke-width="1.4"/><path d="M5 8h6" stroke="var(--t-3)" stroke-width="1.4" stroke-linecap="round"/>'),
};
const ICON = {
  add: () => svg('<path d="M8 3.5v9M3.5 8h9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>', 14),
  down: () => svg('<path d="M8 2.8v7.4M4.8 7.2L8 10.4l3.2-3.2M3 13.2h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>', 14),
  caret: () => svg('<path d="M4.5 6.5L8 10l3.5-3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>', 12),
  close: () => svg('<path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>', 14),
  file: () => svg('<path d="M4 1.8h5.2L12.5 5v9.2H4z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 1.8V5h3.5M6 8.5h4.5M6 11h4.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>', 20),
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat(Infinity)) if (child != null && child !== false) node.append(child); // strings are text, never markup
  return node;
}

const money = (v) => (v == null ? '' : `${v < 0 ? '−' : ''}£${Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const day = (iso, year = true) => (iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(year ? { year: 'numeric' } : {}), timeZone: 'UTC' }) : '');
const period = (a, b) => {
  if (!a) return '';
  const sameYear = a.slice(0, 4) === (b || '').slice(0, 4);
  return `${day(a, !sameYear)} to ${day(b)}`;
};
const count = (n) => n.toLocaleString('en-GB');

function ring(done, total) {
  const r = 6.3, c = 2 * Math.PI * r, part = total ? done / total : 0;
  return svg(`<circle cx="8" cy="8" r="${r}" stroke="var(--t-line)" stroke-width="2"/>`
    + `<circle cx="8" cy="8" r="${r}" stroke="var(--t-accent)" stroke-width="2" stroke-linecap="round" stroke-dasharray="${(part * c).toFixed(2)} ${c.toFixed(2)}" transform="rotate(-90 8 8)"/>`);
}

function bankChip(name) {
  if (!name) return el('span');
  const [label, colour] = BANKS[name.toLowerCase()] || [name, 'var(--t-3)'];
  const dot = el('i'); dot.style.background = colour;
  return el('span', { class: 'chip c-bank' }, dot, label);
}

// ---- Files in, results out ------------------------------------------------------------------------------------

function add(chosen) {
  const pdfs = chosen.filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
  if (!pdfs.length) return;
  const taken = new Set(state.files.map((f) => f.name));
  Promise.all(pdfs.map((file) => file.arrayBuffer())).then((buffers) => {
    pdfs.forEach((file, i) => {
      // Two files with the same name are told apart: the results cite each by name
      let name = file.name.replace(/[\\/]/g, '_');
      for (let n = 2; taken.has(name); n += 1) name = file.name.replace(/[\\/]/g, '_').replace(/(\.pdf)?$/i, ` (${n})$1`);
      taken.add(name);
      state.files.push({ name, bytes: buffers[i], status: 'queued', row: null });
    });
    run();
  });
}

function remove(name) {
  state.files = state.files.filter((f) => f.name !== name);
  if (state.open === name) state.open = null;
  if (state.files.length) run(); else reset();
}

function reset() {
  for (const url of state.urls) URL.revokeObjectURL(url);
  Object.assign(state, { files: [], result: null, outputs: {}, urls: [], open: null, busy: false, error: '' });
  render();
}

function run() {
  // The whole set runs together: how statements join and which transfers match depend on all of them
  state.busy = true; state.error = '';
  for (const f of state.files) f.status = 'queued';
  render();
  const files = state.files.map((f) => ({ name: f.name, bytes: f.bytes.slice(0) }));
  worker.postMessage({ type: 'run', files }, files.map((f) => f.bytes));
}

worker.onmessage = ({ data }) => {
  if (data.type === 'status') {
    state.engine = data.message === 'Ready' ? 'ready' : 'loading';
  } else if (data.type === 'progress') {
    // Reading reports a file as it starts; reconciling reports one as it finishes, and the next one starts
    const at = data.stage === 'read' ? data.done : data.done - 1;
    state.files.forEach((f, i) => {
      if (data.stage === 'read') f.status = i < at ? 'read' : i === at ? 'reading' : 'queued';
      else f.status = i <= at ? 'checked' : i === at + 1 ? 'reconciling' : 'read';
    });
  } else if (data.type === 'result') {
    state.busy = false; state.engine = 'ready';
    for (const url of state.urls) URL.revokeObjectURL(url);
    state.urls = [];
    state.result = data.result; state.outputs = data.outputs;
    const byName = new Map(data.result.statements.map((s) => [s.file, s]));
    for (const f of state.files) { f.row = byName.get(f.name) || null; f.status = 'done'; }
  } else if (data.type === 'error') {
    state.busy = false; state.error = data.message;
    for (const f of state.files) if (f.status !== 'done') f.status = 'queued';
  }
  render();
};

// ---- Rendering -----------------------------------------------------------------------------------------------

const DOWNLOADS = [
  ['statements.xlsx', 'Workbook', 'xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  ['all_transactions.csv', 'All transactions', 'csv', 'text/csv'],
  ['batch_report.csv', 'Statements', 'csv', 'text/csv'],
  ['coverage_report.csv', 'How statements join', 'csv', 'text/csv'],
  ['transfers.csv', 'Transfers between accounts', 'csv', 'text/csv'],
];

function link(name, cls, ...content) {
  const spec = DOWNLOADS.find((d) => d[0] === name);
  const url = URL.createObjectURL(new Blob([state.outputs[name]], { type: spec[3] }));
  state.urls.push(url);
  return el('a', { class: cls, href: url, download: `tothepenny-${name.replace('_', '-')}` }, ...content);
}

function header() {
  const n = state.files.length;
  const engine = el('span', { class: `engine ${state.engine}`, title: 'Statements are read in this browser tab. Nothing is uploaded.' },
    el('i'), el('span', {}, state.engine === 'ready' ? 'On this computer' : 'Loading reader'));
  const actions = [engine];
  if (n) actions.push(el('button', { class: 'btn ghost', type: 'button', onclick: reset, disabled: state.busy }, 'Clear'));
  if (n) actions.push(el('button', { class: 'btn', type: 'button', onclick: () => input.click() }, ICON.add(), 'Add'));
  if (state.result && !state.busy && state.outputs['statements.xlsx']) {
    for (const url of state.urls) URL.revokeObjectURL(url);
    state.urls = [];
    actions.push(link('statements.xlsx', 'btn primary', ICON.down(), 'Workbook'));
    const others = DOWNLOADS.slice(1).filter(([file]) => state.outputs[file]);
    const panel = el('div', { hidden: true }, others.map(([file, label, ext]) => link(file, '', label, el('span', {}, `.${ext}`))));
    const toggle = el('button', { class: 'btn', type: 'button', 'aria-haspopup': 'true', onclick: (e) => { e.stopPropagation(); panel.hidden = !panel.hidden; } }, 'CSV', ICON.caret());
    actions.push(el('span', { class: 'menu' }, toggle, panel));
  }
  return el('div', { class: 'head' },
    el('h2', {}, 'Statements', n ? el('span', { class: 'n' }, String(n)) : null),
    el('span', { class: 'sp' }), actions);
}

function empty() {
  return el('div', { class: 'empty' },
    el('div', { class: 'ic' }, ICON.file()),
    el('b', {}, 'Drop your statements here'),
    el('p', {}, 'Every month, every account, all at once.'),
    el('button', { class: 'btn primary', type: 'button', onclick: () => input.click() }, ICON.add(), 'Choose files'));
}

function stats() {
  const r = state.result;
  if (!r || state.busy) return null;
  const reconciled = r.statements.filter((s) => s.reconciled).length;
  const gaps = r.issues.filter((i) => i.kind === 'missing period' || i.kind === 'balance jump').length;
  return el('div', { class: 'stats' },
    el('span', { class: 'stat', title: "Statements whose transactions add up to the balances the bank printed" },
      ring(reconciled, r.statements.length), el('b', {}, `${reconciled}/${r.statements.length}`), 'reconciled'),
    el('span', { class: 'stat' }, el('b', {}, count(r.transactions)), 'transactions'),
    el('span', { class: `stat${gaps ? ' warn' : ''}`, title: 'Places where one statement does not lead into the next for the same account' },
      el('b', {}, String(gaps)), gaps === 1 ? 'gap' : 'gaps'),
    r.transfers ? el('span', { class: 'stat', title: 'Payments out of one account here matched to their arrival in another' },
      el('b', {}, String(r.transfers)), r.transfers === 1 ? 'transfer matched' : 'transfers matched') : null);
}

function row(file) {
  const s = file.row;
  const status = file.status;
  const glyph = status === 'done' ? (s && s.reconciled ? GLYPH.done() : GLYPH.failed())
    : status === 'queued' || status === 'read' ? GLYPH.queued() : status === 'checked' ? GLYPH.done() : GLYPH.working();
  const working = { queued: 'Waiting', reading: 'Reading', read: 'Read', reconciling: 'Reconciling', checked: 'Reconciled' }[status];
  const title = s && !s.reconciled ? s.why : working || null;
  const open = state.open === file.name;
  const cells = el('div', {
    class: `row${open ? ' open' : ''}${s ? (s.reconciled ? ' ok' : ' bad') : ''}`, title, role: 'button', tabindex: '0', 'aria-expanded': String(open),
    onclick: () => { if (s) { state.open = open ? null : file.name; render(); } },
    onkeydown: (e) => { if ((e.key === 'Enter' || e.key === ' ') && s) { e.preventDefault(); state.open = open ? null : file.name; render(); } },
  },
  glyph,
  el('span', { class: 'name' }, file.name),
  s ? (s.reconciled ? bankChip(s.bank) : el('span', { class: 'chip red c-bank' }, s.short || 'Not reconciled')) : el('span', { class: 'muted c-bank' }, working),
  el('span', { class: 'muted mono c-account' }, s ? s.account : ''),
  el('span', { class: 'muted c-period' }, s ? period(s.from, s.to) : ''),
  el('span', { class: 'num muted c-count', title: s && s.reconciled ? 'Transactions' : null }, s && s.reconciled ? count(s.transactions) : ''),
  el('span', { class: 'num c-close', title: s && s.closing != null ? 'Closing balance' : null }, s && s.reconciled ? money(s.closing) : ''),
  el('button', { class: 'x', type: 'button', title: 'Remove', 'aria-label': `Remove ${file.name}`, disabled: state.busy,
    onclick: (e) => { e.stopPropagation(); remove(file.name); } }, ICON.close()));
  if (!open || !s) return cells;
  const props = [
    ['Status', s.reconciled ? 'Reconciled with the balances the bank printed' : 'Not reconciled'],
    s.bank ? ['Bank', s.bank] : null,
    s.account ? ['Account', s.account] : null,
    s.from ? ['Period', period(s.from, s.to)] : null,
    s.opening != null ? ['Opening balance', money(s.opening)] : null,
    s.closing != null ? ['Closing balance', money(s.closing)] : null,
    s.reconciled ? ['Transactions', count(s.transactions)] : null,
    s.reconciled ? null : ['Why', s.why],
  ].filter(Boolean);
  return [cells, el('div', { class: 'peek' }, el('dl', { class: 'props' },
    props.map(([k, v]) => [el('dt', {}, k), el('dd', { class: k === 'Why' ? 'why' : null }, v)])))];
}

function group(label, glyph, items) {
  return items.length ? [el('div', { class: 'group' }, glyph, label, el('span', { class: 'n' }, String(items.length))), items] : [];
}

function body() {
  if (!state.files.length) return [empty()];
  const done = state.files.filter((f) => f.status === 'done' && f.row);
  const working = state.files.filter((f) => !(f.status === 'done' && f.row));
  const reconciled = done.filter((f) => f.row.reconciled);
  const not = done.filter((f) => !f.row.reconciled);
  const issues = state.result && !state.busy ? state.result.issues : [];
  return [
    state.error ? el('div', { class: 'err' }, `Something went wrong: ${state.error}`) : null,
    stats(),
    group('In progress', GLYPH.working(), working.map(row)),
    group('Not reconciled', GLYPH.failed(), not.map(row)),
    group('Reconciled', GLYPH.done(), reconciled.map(row)),
    group('Where statements do not join', GLYPH.gap(), issues.map((i) => el('div', { class: 'gaprow', title: i.text },
      i.kind === 'missing period' || i.kind === 'balance jump' ? GLYPH.gap() : GLYPH.info(),
      el('div', { class: 't' }, i.text),
      el('span', { class: `chip${i.kind === 'missing period' || i.kind === 'balance jump' ? ' amber' : ''}` },
        { 'missing period': 'Missing period', 'balance jump': 'Balance jump', duplicate: 'Duplicate', overlap: 'Overlap' }[i.kind] || i.kind)))),
  ];
}

const style = document.createElement('style');
style.textContent = CSS;
document.head.append(style);
if (!document.querySelector('link[data-ttp-font]')) {
  document.head.append(el('link', { rel: 'stylesheet', 'data-ttp-font': '1',
    href: 'https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400..700&display=swap' }));
}

const input = el('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: true, hidden: true,
  onchange: () => { add([...input.files]); input.value = ''; } });
const sheet = el('div', { class: 'sheet' });
root.classList.add('ttp');
root.replaceChildren(input, sheet);

let depth = 0;
sheet.addEventListener('dragenter', (e) => { e.preventDefault(); depth += 1; sheet.classList.add('over'); });
sheet.addEventListener('dragover', (e) => e.preventDefault());
sheet.addEventListener('dragleave', () => { depth -= 1; if (depth <= 0) { depth = 0; sheet.classList.remove('over'); } });
sheet.addEventListener('drop', (e) => { e.preventDefault(); depth = 0; sheet.classList.remove('over'); add([...e.dataTransfer.files]); });
document.addEventListener('click', () => sheet.querySelectorAll('.menu > div').forEach((m) => { m.hidden = true; }));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && state.open) { state.open = null; render(); } });

function render() {
  sheet.replaceChildren(header(), ...body().flat(Infinity).filter(Boolean), el('div', { class: 'drop' }, 'Drop to add'));
}

render();
worker.postMessage({ type: 'warm' }); // load the reader while the page is read
