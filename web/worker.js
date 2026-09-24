// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

// Runs off the page's main thread: PDFium (WebAssembly) reads each PDF, Pyodide runs tothepenny's Python on the
// readings, and the output files come back to the page as bytes. No network requests except loading this code.

import { readPdf, wrapPdfium } from './pdf-read.js';

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/';
const here = (file) => new URL(file, import.meta.url).href;
const say = (message) => postMessage({ type: 'status', message });

let ready = null;

async function loadPdfium() {
  const [script, wasm] = await Promise.all([
    fetch(here('pdfium.js')).then((r) => r.text()),
    fetch(here('pdfium.wasm')).then((r) => r.arrayBuffer()),
  ]);
  return new Promise((resolve) => {
    const Module = { wasmBinary: wasm, onRuntimeInitialized: () => resolve(wrapPdfium(Module)) };
    new Function('Module', script)(Module); // pdfium-binaries' Emscripten build, given its own Module object
  });
}

async function loadPython() {
  const { loadPyodide } = await import(`${PYODIDE}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(['pyyaml', 'python-dateutil', 'micropip']);
  const manifest = await fetch(here('manifest.json')).then((r) => r.json());
  const micropip = py.pyimport('micropip');
  for (const wheel of manifest.wheels) await micropip.install(here(wheel), { deps: false });
  const zip = await fetch(here(manifest.package)).then((r) => r.arrayBuffer());
  py.unpackArchive(zip, 'zip', { extractDir: '/lib/tothepenny-src' });
  py.runPython(`
import sys, logging
sys.path.insert(0, '/lib/tothepenny-src')
logging.disable(logging.CRITICAL)
from tothepenny import browser
`);
  return py;
}

async function start() {
  say('Loading the reader (about 15 MB, once)…');
  const [P, py] = await Promise.all([loadPdfium(), loadPython()]);
  say('Ready');
  return { P, py };
}

onmessage = async (event) => {
  const { type, files } = event.data;
  try {
    if (!ready) ready = start();
    const { P, py } = await ready;
    if (type === 'warm') return;
    const FS = py.FS;
    for (const dir of ['/work', '/work/in', '/work/json']) if (!FS.analyzePath(dir).exists) FS.mkdir(dir);
    for (const dir of ['/work/in', '/work/json']) for (const f of FS.readdir(dir)) if (f !== '.' && f !== '..') FS.unlink(`${dir}/${f}`);

    const names = [];
    const unreadable = {};
    for (const [i, file] of files.entries()) {
      postMessage({ type: 'progress', stage: 'read', done: i, total: files.length, name: file.name });
      const bytes = new Uint8Array(file.bytes);
      FS.writeFile(`/work/in/${file.name}`, bytes);
      try {
        FS.writeFile(`/work/json/${file.name}.json`, JSON.stringify(readPdf(P, bytes)));
      } catch (error) {
        unreadable[file.name] = /error 4/.test(error.message) ? 'it needs a password to open (a statement locked only against editing or printing reads normally)' : 'it is not a readable PDF';
      }
      names.push(file.name);
    }

    const progress = (done, total, name) => postMessage({ type: 'progress', stage: 'reconcile', done, total, name });
    const result = JSON.parse(py.globals.get('browser').run(py.toPy(names), '/work', progress, py.toPy(unreadable)));
    const outputs = {};
    for (const name of result.outputs) outputs[name] = FS.readFile(`/work/out/${name}`);
    postMessage({ type: 'result', result, outputs }, Object.values(outputs).map((b) => b.buffer));
  } catch (error) {
    postMessage({ type: 'error', message: String(error && error.message || error) });
  }
};
