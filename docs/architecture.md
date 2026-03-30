# Architecture

Spack-AI Diagnostic Bridge keeps the original evaluation architecture intact and
adds a CI-oriented extension around it.

## Layers

1. `eval/`
   Handles Spack metadata extraction, token-bounded summarization, prompt
   preparation, LLM-backed OLE scenario generation, and scenario deduplication.
2. `ci_extension/`
   Consumes generated scenarios, scores them, schedules the most valuable tests,
   simulates CI outcomes, and feeds those results back into future prioritization.
3. `shared/`
   Centralizes path configuration, JSON/logging utilities, and the weighted
   `spec_distance()` implementation so both layers use the same similarity model.

## Design Intent

- Keep the GSoC qualification pipeline readable and standalone.
- Make the CI extension additive rather than invasive.
- Preserve deterministic fallback behavior for no-API and no-Spack environments.
- Keep all intermediate artifacts JSON-based for CI transport and inspection.

## Incremental Behavior

`eval/summarize.py` stores metadata fingerprints in `eval/analysis_cache.json`.
If a package fingerprint is unchanged, downstream CI stages can skip fresh LLM
analysis for that package set.
