// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

// The page: choose statements, watch them read and reconciled, download the results. The work happens in worker.js;
// the downloads are made here from bytes the worker hands back, so nothing is stored or sent anywhere.

const root = document.getElementById('tothepenny');
const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
let urls = [];

const CSS = `
.ttp { --ttp-fg: var(--foreground, #0a1628); --ttp-muted: var(--ink-muted, #3e5266); --ttp-line: var(--line, rgba(14,30,50,.12));
  --ttp-accent: var(--accent, #16a34a); --ttp-accent-soft: var(--accent-soft, rgba(22,163,74,.12)); --ttp-red: var(--red, #d33);
  --ttp-red-soft: var(--red-soft, rgba(211,51,51,.1)); --ttp-amber: var(--amber, #b7791f); --ttp-amber-soft: var(--amber-soft, rgba(183,121,31,.12));
  color: var(--ttp-fg); font-family: var(--font-body, system-ui, sans-serif); }
.ttp * { box-sizing: border-box; }
.ttp-drop { border: 1.5px dashed var(--ttp-line); border-radius: 14px; padding: 2.5rem 1.25rem; text-align: center;
  transition: border-color .15s, background .15s; cursor: pointer; }
.ttp-drop:hover, .ttp-drop.over { border-color: var(--ttp-accent); background: var(--ttp-accent-soft); }
.ttp-drop strong { display: block; font-size: 1.15rem; margin-bottom: .35rem; }
.ttp-drop span { color: var(--ttp-muted); font-size: .95rem; }
.ttp-status { margin: .9rem 0 0; color: var(--ttp-muted); font-size: .9rem; min-height: 1.3em; }
.ttp-bar { height: 4px; border-radius: 2px; background: var(--ttp-line); overflow: hidden; margin-top: .6rem; }
.ttp-bar > div { height: 100%; width: 0; background: var(--ttp-accent); transition: width .2s; }
.ttp-summary { font-size: 1.2rem; font-weight: 600; margin: 1.6rem 0 .3rem; }
.ttp-sub { color: var(--ttp-muted); margin: 0 0 1rem; }
.ttp-downloads { display: flex; flex-wrap: wrap; gap: .5rem; margin: 1rem 0 1.5rem; }
.ttp-btn { display: inline-flex; align-items: center; gap: .4rem; padding: .55rem .9rem; border-radius: 9px; font: inherit;
  font-size: .92rem; font-weight: 600; text-decoration: none; border: 1px solid var(--ttp-line); color: var(--ttp-fg);
  background: transparent; cursor: pointer; }
.ttp-btn:hover { border-color: var(--ttp-accent); }
.ttp-btn.primary { background: var(--ttp-accent); border-color: var(--ttp-accent); color: #fff; }
.ttp-table-wrap { overflow-x: auto; border: 1px solid var(--ttp-line); border-radius: 12px; }
.ttp table { width: 100%; border-collapse: collapse; font-size: .88rem; }
.ttp th, .ttp td { text-align: left; padding: .55rem .7rem; border-bottom: 1px solid var(--ttp-line); vertical-align: top; }
.ttp th { font-weight: 600; color: var(--ttp-muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; }
.ttp tr:last-child td { border-bottom: 0; }
.ttp td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ttp td.file { min-width: 12rem; max-width: 20rem; overflow-wrap: anywhere; }
.ttp td.file .ttp-chip { margin-top: .3rem; }
.ttp tr.has-why td { border-bottom: 0; padding-bottom: .2rem; }
.ttp tr.why td { color: var(--ttp-muted); font-size: .84rem; padding-top: 0; }
.ttp tr.why td > div { position: sticky; left: .7rem; max-width: min(40rem, calc(100vw - 4rem)); }
.ttp-more { width: 100%; font-size: .9rem; }
.ttp-more summary { cursor: pointer; color: var(--ttp-muted); margin: .2rem 0; }
.ttp-more div { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .5rem; }
.ttp-chip { display: inline-block; padding: .12rem .5rem; border-radius: 99px; font-size: .78rem; font-weight: 600; white-space: nowrap; }
.ttp-chip.ok { background: var(--ttp-accent-soft); color: var(--ttp-accent); }
.ttp-chip.no { background: var(--ttp-red-soft); color: var(--ttp-red); }
.ttp-why { color: var(--ttp-muted); font-size: .82rem; margin-top: .25rem; }
.ttp h3 { font-size: 1rem; margin: 1.6rem 0 .5rem; }
.ttp-issues { margin: 0; padding-left: 1.1rem; }
.ttp-issues li { margin: .35rem 0; }
.ttp-issues li.gap::marker { color: var(--ttp-amber); }
.ttp-error { color: var(--ttp-red); margin-top: 1rem; }
.ttp-hidden { display: none; }
`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null) node.append(child); // strings are text, never markup
  return node;
}

const money = (v) => (v == null ? '' : `${v < 0 ? '-' : ''}£${Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const day = (iso) => (iso ? new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '');
const plural = (n, one, many) => `${n.toLocaleString('en-GB')} ${n === 1 ? one : many}`;

const style = document.createElement('style');
style.textContent = CSS;
document.head.append(style);

const input = el('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: '', class: 'ttp-hidden' });
const drop = el('div', { class: 'ttp-drop', role: 'button', tabindex: '0' },
  el('strong', {}, 'Drop bank statement PDFs here'),
  el('span', {}, 'or click to choose them. Several at once is fine: a year of statements, or several accounts.'));
const status = el('p', { class: 'ttp-status', 'aria-live': 'polite' }, 'Getting the reader ready…');
const bar = el('div', { class: 'ttp-bar ttp-hidden' }, el('div'));
const results = el('div', { class: 'ttp-results' });
root.classList.add('ttp');
root.replaceChildren(input, drop, status, bar, results);

drop.addEventListener('click', () => input.click());
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); start([...e.dataTransfer.files]); });
input.addEventListener('change', () => { start([...input.files]); input.value = ''; });

let busy = false;
async function start(chosen) {
  const pdfs = chosen.filter((f) => /\.pdf$/i.test(f.name) || f.type === 'application/pdf');
  if (!pdfs.length || busy) return;
  busy = true;
  clear();
  const seen = new Map();
  const files = [];
  for (const file of pdfs) {
    // Two files with the same name get told apart: the results cite each by name
    const base = file.name.replace(/[\\/]/g, '_');
    const n = (seen.get(base) || 0) + 1;
    seen.set(base, n);
    files.push({ name: n === 1 ? base : base.replace(/(\.pdf)?$/i, ` (${n})$1`), bytes: await file.arrayBuffer() });
  }
  bar.classList.remove('ttp-hidden');
  status.textContent = `Reading ${plural(files.length, 'statement', 'statements')}…`;
  worker.postMessage({ type: 'run', files }, files.map((f) => f.bytes));
}

function clear() {
  for (const url of urls) URL.revokeObjectURL(url);
  urls = [];
  results.replaceChildren();
}

const LABELS = {
  'statements.xlsx': ['Workbook (.xlsx)', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', true],
  'all_transactions.csv': ['All transactions (.csv)', 'text/csv'],
  'batch_report.csv': ['Statements (.csv)', 'text/csv'],
  'coverage_report.csv': ['How statements join (.csv)', 'text/csv'],
  'transfers.csv': ['Transfers between accounts (.csv)', 'text/csv'],
};

function show(result, outputs) {
  const reconciled = result.statements.filter((s) => s.reconciled).length;
  const total = result.statements.length;
  const gaps = result.issues.filter((i) => i.kind === 'missing period' || i.kind === 'balance jump');
  const summary = reconciled === total
    ? `All ${plural(total, 'statement', 'statements')} reconcile with the bank's printed balances`
    : `${reconciled} of ${plural(total, 'statement', 'statements')} reconcile with the bank's printed balances`;
  const sub = [
    plural(result.transactions, 'transaction', 'transactions'),
    result.joins || gaps.length ? (gaps.length ? plural(gaps.length, 'gap between statements', 'gaps between statements') : 'every statement joins the next') : null,
    result.transfers ? plural(result.transfers, 'transfer between accounts matched', 'transfers between accounts matched') : null,
  ].filter(Boolean).join(' · ');

  const link = (name) => {
    const [label, type, primary] = LABELS[name];
    const url = URL.createObjectURL(new Blob([outputs[name]], { type }));
    urls.push(url);
    return el('a', { class: `ttp-btn${primary ? ' primary' : ''}`, href: url, download: `tothepenny-${name.replace('_', '-')}` }, label);
  };
  const main = ['statements.xlsx', 'all_transactions.csv'].filter((n) => outputs[n]);
  const more = Object.keys(LABELS).filter((n) => outputs[n] && !main.includes(n));
  const downloads = el('div', { class: 'ttp-downloads' }, main.map(link),
    el('button', { class: 'ttp-btn', type: 'button', onclick: () => { clear(); status.textContent = 'Ready. Your statements stay on this computer.'; } }, 'Start again'),
    more.length ? el('details', { class: 'ttp-more' }, el('summary', {}, 'Other files (the workbook has all of these)'), el('div', {}, more.map(link))) : null);

  const rows = result.statements.flatMap((s) => [
    el('tr', { class: s.reconciled ? '' : 'has-why' },
      el('td', { class: 'file' }, el('div', {}, s.file),
        el('span', { class: `ttp-chip ${s.reconciled ? 'ok' : 'no'}` }, s.reconciled ? 'Reconciled' : 'Not reconciled')),
      el('td', {}, s.bank),
      el('td', {}, s.account),
      el('td', {}, s.from ? `${day(s.from)} to ${day(s.to)}` : ''),
      el('td', { class: 'num' }, money(s.opening)),
      el('td', { class: 'num' }, money(s.closing)),
      el('td', { class: 'num' }, s.reconciled ? String(s.transactions) : '')),
    s.reconciled ? null : el('tr', { class: 'why' }, el('td', { colspan: '7' }, el('div', {}, s.why))),
  ].filter(Boolean));

  results.replaceChildren(...[
    el('p', { class: 'ttp-summary' }, summary),
    el('p', { class: 'ttp-sub' }, sub),
    downloads,
    el('div', { class: 'ttp-table-wrap' }, el('table', {},
      el('thead', {}, el('tr', {}, ...['Statement', 'Bank', 'Account', 'Period', 'Opening', 'Closing', 'Transactions'].map((h) => el('th', {}, h)))),
      el('tbody', {}, rows))),
    result.issues.length ? el('h3', {}, 'Where the statements do not join up') : null,
    result.issues.length ? el('ul', { class: 'ttp-issues' }, result.issues.map((i) => el('li', { class: i.kind === 'missing period' || i.kind === 'balance jump' ? 'gap' : '' }, i.text))) : null,
  ].filter(Boolean));
}

worker.onmessage = ({ data }) => {
  if (data.type === 'status') {
    if (!busy) status.textContent = data.message === 'Ready' ? 'Ready. Your statements stay on this computer.' : data.message;
  } else if (data.type === 'progress') {
    const verb = data.stage === 'read' ? 'Reading' : 'Reconciling';
    const done = data.stage === 'read' ? data.done : data.done;
    status.textContent = `${verb} ${Math.min(done + (data.stage === 'read' ? 1 : 0), data.total)} of ${data.total}: ${data.name}`;
    const fraction = data.stage === 'read' ? (data.done / data.total) * 0.2 : 0.2 + (data.done / data.total) * 0.8;
    bar.firstChild.style.width = `${Math.round(fraction * 100)}%`;
  } else if (data.type === 'result') {
    busy = false;
    bar.classList.add('ttp-hidden');
    bar.firstChild.style.width = '0';
    status.textContent = 'Done. Nothing was uploaded: the downloads below were made in this tab.';
    show(data.result, data.outputs);
  } else if (data.type === 'error') {
    busy = false;
    bar.classList.add('ttp-hidden');
    status.textContent = '';
    results.replaceChildren(el('p', { class: 'ttp-error' }, `Something went wrong: ${data.message}`));
  }
};

worker.postMessage({ type: 'warm' }); // load the reader while the page is read
