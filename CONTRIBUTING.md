# Contributing

Issues and pull requests are welcome.

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Ground rules:

- **The XML and the hash are byte-sensitive.** ZATCA recomputes the invoice hash over the document's exact text. A change to element order, indentation or number formatting changes every hash. `tests/test_reference_parity.py` must keep passing; if a change is intended, explain why and how it was verified against ZATCA.
- **Money is decimal.** Use the helpers in `zatca_tools/_rounding.py`, never Python's `round()` (it rounds half to even, on binary values).
- **No network except ZATCA, no telemetry, no logging of documents or secrets.**
- Tests that talk to ZATCA belong in `tests/test_sandbox.py` (opt-in) and use only the sandbox's published test values.
- Keep messages in English and name the field they are about.
- Every code snippet in the documentation and the README is checked against the real API (`tests/test_docs_snippets.py`); a renamed method fails it.
- The PDF must stay PDF/A-3b. Run veraPDF before a release: `ZATCA_SDK_VERAPDF=/path/to/verapdf pytest tests/test_pdf.py -k verapdf`.

## Before a release

1. Refresh `src/zatca_tools/data/error_reference.json`, the list of ZATCA codes the error reference documents (maintainers export it from the ZATCA Tools platform).
2. `pytest`, then `ZATCA_SDK_SANDBOX=1 pytest tests/test_sandbox.py`, then the veraPDF test.
3. Run the three examples against the sandbox.
