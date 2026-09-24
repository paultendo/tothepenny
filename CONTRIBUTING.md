# Contributing

Contributions are welcome, especially support for new banks and new statement layouts.

## How to contribute

1. **Start with an issue** if you are adding a bank or a layout, so we can agree the approach first. Use the "New
   bank or statement layout" template. Describe the layout; never attach a statement.
2. **Fork** the repository on GitHub and create a branch.
3. **Make your change**, with synthetic tests (see below), and run `python -m pytest`.
4. **Open a pull request** against `main`. The checklist in the pull request template covers what is needed.
5. **Agree to the Contributor Licence Agreement.** On your first pull request a bot asks you to reply with a
   sentence confirming you agree to [CLA.md](CLA.md). You only do this once.
6. **Checks and review.** The tests run automatically on every pull request. The maintainer, Paul Wood FRSA
   ([@paultendo](https://github.com/paultendo)), reviews and merges.

## The one rule

**A statement either reconciles with the bank's own figures or its transactions are left out.** A parser's output must
reconcile to the statement's printed balances: opening balance plus money in minus money out equals the closing
balance, and where the bank prints running or day-end balances, every one of those must be met too. Where it cannot
reconcile, the tool says so rather than returning a plausible guess. Please keep it that way.

## Adding or fixing a bank

Each bank has two parts:

1. **A template** in `tothepenny/bank_templates/<bank>.yaml`: how to recognise the bank and read its header
   fields (period, opening and closing balances, totals).
2. **A parser** in `tothepenny/parsers/<bank>_parser.py`, when the generic parser is not enough. Decide
   money in and money out from the bank's balances wherever the statement prints them, not from column positions
   alone. Column positions vary across pages and between layouts.

Add tests in `tests/test_parsers/test_<bank>_parser.py` using **synthetic** statement text that reproduces the
layout, with the same column widths, made-up names and made-up numbers. See `test_metro_parser.py` and
`test_hsbc_balance_chain.py` for the pattern.

## Never commit real statements

Never add real bank statements, extracts, spreadsheets or any personal data to the repository, to an issue or to a
pull request. That includes names, addresses, account numbers, sort codes, payees and references. If you need to
report a layout problem, describe the layout or build a synthetic example. `.gitignore` excludes PDFs and output
folders, but it is your responsibility.

## Licence

tothepenny is dual-licensed: AGPL-3.0 and commercial. See [COMMERCIAL.md](COMMERCIAL.md). The
Contributor Licence Agreement is what allows your contribution to be included in both. You keep the copyright in
your contribution.

## Running the tests

```bash
pip install -e ".[dev]"
python -m pytest
```
