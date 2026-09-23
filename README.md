# statement-reconciler

Turn bank statement PDFs into transactions you can rely on, on your own machine, with every statement checked
against the bank's own figures.

Solicitors, advisers, accountants and anyone else who has to read someone's bank statements need the numbers to be
right. Most tools guess: they read columns, hope, and hand you a spreadsheet that may be quietly wrong.
statement-reconciler does not guess. It reads each statement, then proves the result against what the bank printed:
the opening balance, the closing balance, the totals, and every running or day-end balance on the page. If the
transactions do not add up to the bank's own figures, it tells you, instead of giving you a plausible spreadsheet.

- **Reconciled or refused.** Money in and money out are decided from the bank's balances wherever the statement
  prints them, and every statement is checked to the penny.
- **Local only.** Native PDF text first, then local OCR (Tesseract) for scans. No statement data is sent anywhere,
  and there are no cloud services and no API keys.
- **Bank-aware.** Each bank has a template and, where needed, its own parser, for layouts that change between
  pages, overdrawn markers ("D", "OD", minus signs), balances printed only at the end of each day, and so on.
- **Spreadsheet output.** One workbook per statement, with transactions, running balances, metadata and a
  reconciliation audit, plus a batch summary for whole folders.

## Banks

statement-reconciler is built for **UK** bank statements. It has templates for:

- **UK banks:** Barclays, Halifax, HSBC (and first direct), Lloyds, Metro Bank, Monzo, Nationwide, NatWest, Revolut,
  Santander and TSB.
- **Banks outside the UK:** Crédit Agricole and LCL (France), and PagSeguro (Brazil). These read statements in French
  and Portuguese, with those countries' date and number formats.

How reliable each one is depends on the layouts it has met. A statement that does not reconcile is reported as such.
New banks and layouts are the most useful contribution; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

Requires Python 3.10+ and, for scanned statements, Tesseract. PDFs are read with pdfium (via pypdfium2), which
installs with the package.

```bash
pip install -e .
```

## Use

```bash
# One statement
statement-reconciler extract statement.pdf

# A folder of statements, with a summary of which reconciled
statement-reconciler batch statements/ -o output/

# Supported banks
statement-reconciler banks
```

Each batch writes a `batch_report.csv` showing, for every file, the bank, the number of transactions and whether it
reconciled.

## Licence

Copyright (C) 2026 Paul Wood FRSA ([@paultendo](https://github.com/paultendo)).

statement-reconciler is dual-licensed:

- under the **GNU Affero General Public License v3.0** ([LICENSE](LICENSE)), free of charge; or
- under a **commercial licence**, for use in products or services without the AGPL's obligations. See
  [COMMERCIAL.md](COMMERCIAL.md).

Contributions are accepted under the [Contributor Licence Agreement](CLA.md).

This software is provided without warranty. It is a tool for reading statements, not financial or legal advice.
Check anything you rely on against the original statement.
