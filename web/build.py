# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Build the browser version into web/dist (or a directory given as the first argument).

The page loads, from the same place: the app (app.js, worker.js, pdf-read.js), PDFium compiled to WebAssembly
(pdfium-binaries' build of the same PDFium version pypdfium2 bundles, so the browser reads exactly what the
command line reads), the tothepenny package as a zip, and the pure-Python wheels Pyodide does not ship. Pyodide
itself comes from jsdelivr.

    python3 web/build.py [out_dir]
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
PDFIUM_BUILD = '6462'  # keep in step with the PDFium inside the pinned pypdfium2 (4.30: 6462)
PDFIUM_URL = f'https://github.com/bblanchon/pdfium-binaries/releases/download/chromium%2F{PDFIUM_BUILD}/pdfium-wasm.tgz'
WHEELS = ['openpyxl==3.1.5', 'et_xmlfile==2.0.0']
APP = ['index.html', 'app.js', 'worker.js', 'pdf-read.js']


def fetch_pdfium(out: Path) -> None:
    cache = WEB / 'vendor' / f'pdfium-wasm-{PDFIUM_BUILD}.tgz'
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(PDFIUM_URL) as response:
            cache.write_bytes(response.read())
    with tarfile.open(cache) as archive:
        for member, target in (('lib/pdfium.js', 'pdfium.js'), ('lib/pdfium.wasm', 'pdfium.wasm'),
                               ('LICENSE', 'PDFIUM-LICENSE.txt')):
            data = archive.extractfile(archive.getmember(member) if member in archive.getnames()
                                       else archive.getmember('./' + member)).read()
            (out / target).write_bytes(data)


def fetch_wheels(out: Path) -> list:
    vendor = WEB / 'vendor'
    vendor.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '--only-binary=:all:', '-q', '-d', str(vendor),
                    *WHEELS], check=True)
    names = []
    for spec in WHEELS:
        project, version = spec.split('==')
        wheel = next(vendor.glob(f"{project.replace('-', '_')}-{version}-*.whl"))
        shutil.copy(wheel, out / wheel.name)
        names.append(wheel.name)
    return names


def package_zip(out: Path) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / 'tothepenny').rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.yaml'):
                archive.write(path, path.relative_to(ROOT).as_posix())
    data = buffer.getvalue()
    name = f'tothepenny-{hashlib.sha256(data).hexdigest()[:12]}.zip'  # a new name for each version, so no stale cache
    (out / name).write_bytes(data)
    return name


def main() -> None:
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else WEB / 'dist'
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name in APP:
        shutil.copy(WEB / name, out / name)
    fetch_pdfium(out)
    wheels = fetch_wheels(out)
    package = package_zip(out)
    for name in ('LICENSE', 'NOTICE'):
        shutil.copy(ROOT / name, out / f'{name}.txt')
    (out / 'manifest.json').write_text(json.dumps({'package': package, 'wheels': wheels}, indent=2) + '\n')
    print(f'built {out}: {", ".join(sorted(p.name for p in out.iterdir()))}')


if __name__ == '__main__':
    main()
