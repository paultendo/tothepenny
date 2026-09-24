# tothepenny

**Does it add up?** Drop in PDF bank statements. tothepenny reads every line, checks it against the bank's own
balances, and hands you a spreadsheet.

People who read other people's bank statements (solicitors, advisers, accountants, family members helping with
someone's affairs) need to know whether the numbers they are working from match the statement. tothepenny reads each
statement and then checks what it read against the bank's own figures: the opening and closing balances, the totals,
and every running or day-end balance printed on the page. A statement whose transactions do not add up to those
figures is reported as not reconciled, and its transactions are left out, rather than passed on as a spreadsheet
that looks right.

- **Reconciled or left out.** Money in and money out are taken from the bank's balances wherever the statement
  prints them, and each statement is checked against them.
- **Local only.** Statements are read on your own computer, or in your own browser tab. Nothing is uploaded, and
  there are no cloud services and no API keys.
- **Several statements at once.** Each account's statements are put in date order and checked to see whether they
  join up: where one statement's closing balance is not the next one's opening, or a period is missing, it says so.
  Transfers between accounts in the same set are matched.
- **Spreadsheet output.** One workbook with every transaction (each citing its statement and page), every statement
  and whether it reconciled, the gaps, and the matched transfers; the same as CSV files.

It is a tool for reading statements, not financial or legal advice. Check anything you rely on against the original
statement.

## Use it in your browser

**[paultendo.github.io/tothepenny](https://paultendo.github.io/tothepenny)**: drop in statement PDFs and download
the results. Nothing to install. The page runs the same code as the command line inside your browser tab, using
PDFium compiled to WebAssembly and Python compiled to WebAssembly (Pyodide), so your statements never leave your
computer, and the downloads are made in the tab. The browser version cannot read scanned statements yet (those need
OCR); the command line can.

To host it yourself: `python3 web/build.py` writes the site to `web/dist`, which any static host can serve.

## Banks

tothepenny is built for **UK** bank statements. It has templates for:

- **UK banks:** Barclays, Halifax, HSBC (and first direct), Lloyds, Metro Bank, Monzo, Nationwide, NatWest, Revolut,
  Santander and TSB.
- **Banks outside the UK:** Crédit Agricole and LCL (France), and PagSeguro (Brazil). These read statements in French
  and Portuguese, with those countries' date and number formats.

How well each one works depends on the layouts it has met. A statement that does not reconcile is reported as such.
New banks and layouts are the most useful contribution; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Install the command line

Requires Python 3.10+ and, for scanned statements, Tesseract. PDFs are read with PDFium (via pypdfium2), which
installs with the package.

```bash
pip install -e .
```

```bash
# One statement
tothepenny extract statement.pdf

# A folder of statements: every file, whether it reconciled, the combined transactions, gaps and transfers
tothepenny batch statements/ -o output/

# Supported banks
tothepenny banks
```

A batch writes `statements.xlsx` (everything in one workbook), `all_transactions.csv`, `batch_report.csv`,
`coverage_report.csv` and `transfers.csv`.

## Licence

Copyright (C) 2026 Paul Wood FRSA ([@paultendo](https://github.com/paultendo)).

tothepenny is dual-licensed:

- under the **GNU Affero General Public License v3.0** ([LICENSE](LICENSE)), free of charge; or
- under a **commercial licence**, for use in products or services without the AGPL's obligations. See
  [COMMERCIAL.md](COMMERCIAL.md).

Contributions are accepted under the [Contributor Licence Agreement](CLA.md).

If tothepenny saves you time, you can [buy me a coffee](https://buymeacoffee.com/paultendo).

This software is provided without warranty.
