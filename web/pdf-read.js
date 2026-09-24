// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

// Reads a PDF in the browser with PDFium compiled to WebAssembly, giving the Python page reader exactly what
// pypdfium2 gives it on a desktop: each character's code, whether PDFium generated it, its loose box and font size,
// the page's thin drawn lines, and the page's bounded text. The WebAssembly build is pdfium-binaries' build of the
// same PDFium version pypdfium2 bundles, so the characters, and the results, are the same. Nothing leaves the browser.

// PDFium's C functions from an Emscripten module (pdfium-binaries' pdfium.js), under their own names.
export function wrapPdfium(Module) {
  const P = {};
  for (const key of Object.keys(Module)) if (/^_FPDF/.test(key)) P[key.slice(1)] = Module[key];
  const malloc = Module._malloc || Module.asm.malloc, free = Module._free || Module.asm.free;
  P.pdfium = {
    get HEAPU8() { return Module.HEAPU8; },
    wasmExports: { malloc, free },
    UTF16ToString(ptr, bytes) {
      const units = new Uint16Array(Module.HEAPU8.buffer, ptr, bytes / 2);
      let text = '';
      for (let i = 0; i < units.length; i += 4096) text += String.fromCharCode(...units.subarray(i, i + 4096));
      return text;
    },
  };
  P.FPDF_InitLibrary();
  return P;
}

const FPDF_PAGEOBJ_PATH = 2;
const FPDF_PAGEOBJ_FORM = 5;
const MAX_DEPTH = 2; // as pypdfium2's get_objects: the page's objects and one level into form objects

// 0 if PDFium can open the file; otherwise its error code (4: it needs a password to open)
export function openError(P, bytes) {
  const M = P.pdfium;
  const data = M.wasmExports.malloc(bytes.length);
  M.HEAPU8.set(bytes, data);
  const doc = P.FPDF_LoadMemDocument(data, bytes.length, 0);
  const error = doc ? 0 : P.FPDF_GetLastError() || 3;
  if (doc) P.FPDF_CloseDocument(doc);
  M.wasmExports.free(data);
  return error;
}

export function readPdf(P, bytes) {
  const M = P.pdfium;
  const heap = () => M.HEAPU8;
  const f32 = (ptr) => new Float32Array(M.HEAPU8.buffer, ptr, 1)[0];
  const data = M.wasmExports.malloc(bytes.length);
  heap().set(bytes, data);
  const doc = P.FPDF_LoadMemDocument(data, bytes.length, 0);
  if (!doc) {
    M.wasmExports.free(data);
    throw new Error(`PDFium could not open the file (error ${P.FPDF_GetLastError()})`);
  }
  const rect = M.wasmExports.malloc(16);
  const bounds = M.wasmExports.malloc(16);
  const pages = [];
  try {
    const count = P.FPDF_GetPageCount(doc);
    for (let index = 0; index < count; index += 1) {
      const page = P.FPDF_LoadPage(doc, index);
      const width = P.FPDF_GetPageWidthF(page);
      const height = P.FPDF_GetPageHeightF(page);
      const text = P.FPDFText_LoadPage(page);

      // Characters: [code, generated, left, right, top, bottom, size], or [code, generated] where PDFium gives no box.
      // FS_RECTF is {left, top, right, bottom}; the Python side turns them into page-down coordinates.
      const chars = [];
      const n = P.FPDFText_CountChars(text);
      for (let i = 0; i < Math.max(n, 0); i += 1) {
        const code = P.FPDFText_GetUnicode(text, i);
        const generated = P.FPDFText_IsGenerated(text, i);
        if (P.FPDFText_GetLooseCharBox(text, i, rect)) {
          chars.push([code, generated, f32(rect), f32(rect + 8), f32(rect + 4), f32(rect + 12), P.FPDFText_GetFontSize(text, i)]);
        } else {
          chars.push([code, generated]);
        }
      }

      // Path objects with their bounds (left, bottom, right, top); null if any could not be located, as the desktop
      // reader then uses no rules for the page.
      let paths = [];
      const walk = (parent, level, isForm) => {
        const objects = isForm ? P.FPDFFormObj_CountObjects(parent) : P.FPDFPage_CountObjects(parent);
        for (let i = 0; i < objects; i += 1) {
          const obj = isForm ? P.FPDFFormObj_GetObject(parent, i) : P.FPDFPage_GetObject(parent, i);
          if (!obj) throw new Error('page object');
          const type = P.FPDFPageObj_GetType(obj);
          if (type === FPDF_PAGEOBJ_PATH) {
            if (!P.FPDFPageObj_GetBounds(obj, bounds, bounds + 4, bounds + 8, bounds + 12)) throw new Error('bounds');
            paths.push([f32(bounds), f32(bounds + 4), f32(bounds + 8), f32(bounds + 12)]);
          }
          if (type === FPDF_PAGEOBJ_FORM && level < MAX_DEPTH - 1) walk(obj, level + 1, true);
        }
      };
      try { walk(page, 0, false); } catch { paths = null; }

      // The page's text within its bounding box, as pypdfium2's get_text_bounded() gives it.
      let bounded = '';
      if (P.FPDF_GetPageBoundingBox(page, rect)) {
        const [left, top, right, bottom] = [f32(rect), f32(rect + 4), f32(rect + 8), f32(rect + 12)];
        const units = P.FPDFText_GetBoundedText(text, left, top, right, bottom, 0, 0);
        if (units > 0) {
          const buffer = M.wasmExports.malloc((units + 1) * 2);
          P.FPDFText_GetBoundedText(text, left, top, right, bottom, buffer, units);
          bounded = M.UTF16ToString(buffer, units * 2);
          M.wasmExports.free(buffer);
        }
      }

      P.FPDFText_ClosePage(text);
      P.FPDF_ClosePage(page);
      pages.push({ width, height, chars, paths, text: bounded });
    }
  } finally {
    P.FPDF_CloseDocument(doc);
    M.wasmExports.free(rect);
    M.wasmExports.free(bounds);
    M.wasmExports.free(data);
  }
  return { pages };
}
