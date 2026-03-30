# Eval Pipeline

This directory contains the original qualification-task pipeline.

## Files

- `summarize.py`: extracts Spack recipe metadata and emits a compact
  `summary.json`
- `generate_scenarios.py`: generates and deduplicates three OLE-risk scenarios
- `summary.json`: token-bounded summary consumed by the LLM prompt
- `scenarios.json`: machine-readable scenario output
- `spack_spec.md`: reviewer-friendly scenario report
- `prompt_logic.md`: prompt design notes

## Commands

```bash
python3 eval/summarize.py --force-static
python3 eval/generate_scenarios.py --mock-only --output-json eval/scenarios.json
```

`analysis_cache.json` is created here automatically so CI can skip unchanged
metadata slices.
