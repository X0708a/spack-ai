# Spack-AI Diagnostic Bridge

Spack-AI Diagnostic Bridge is a CI-oriented research prototype that turns Spack
package metadata into targeted Off-Leading-Edge (OLE) test scenarios.

The pipeline starts with Spack recipe metadata, compresses it into a token-aware
summary for an LLM, generates risky `spack install` configurations, deduplicates
them, and then extends those scenarios into scoring, scheduling, simulated CI,
and feedback-driven reprioritization.

## Key Features

- Token-bounded metadata summarization under 500 tokens
- Live `spack info --json` support with static fallback data
- OLE detection centered on unbounded dependency edges
- LLM-agnostic generation with Anthropic, LM Studio, and deterministic fallback
- Weighted scenario deduplication with SHA-1 fingerprints
- Incremental metadata caching for CI delta runs
- Scenario scoring, batching, CI simulation, and feedback-aware re-ranking
- Exclusion of previously validated specs via `--validated-spec`

## Pipeline

1. `summarize.py`
   Extracts package metadata for `root`, `geant4`, and `clhep`, then writes a
   compact `summary.json`.
2. `generate_scenarios.py`
   Uses the summary as LLM input and emits three deduplicated OLE-risk specs in
   `spack_spec.md` and optional `scenarios.json`.
3. `scoring.py`
   Scores each scenario into `scenario_scores.json` using:
   unbounded dependency count, version distance from latest, rarity, and failure
   history.
4. `scheduler.py`
   Chooses the highest-value scenarios for the next CI run, avoids validated or
   duplicate fingerprints, and groups overlapping dependency graphs into
   `scheduled_tests.json`.
5. `ci_runner.py`
   Simulates CI outcomes in deterministic or mock mode, with optional real
   `spack spec` checks, and writes `ci_results.json`.
6. `feedback.py`
   Updates `failure_history.json`, `validated_specs.json`, and boosts nearby
   scenarios using the existing `spec_distance()` similarity metric.
7. `main.py`
   Runs the end-to-end pipeline in one command for CI-style execution.

## Repository Contents

| File | Description |
|---|---|
| `summarize.py` | Metadata extraction, compression, and fingerprint cache handling |
| `generate_scenarios.py` | LLM-backed OLE generation, validation, and deduplication |
| `scoring.py` | Scenario ranking logic and score artifact generation |
| `scheduler.py` | CI-aware test selection and dependency-overlap batching |
| `ci_runner.py` | Mock, deterministic, or optional real-Spack execution layer |
| `feedback.py` | Failure history updates and feedback-aware score boosting |
| `main.py` | End-to-end orchestration entry point |
| `summary.json` | Compact metadata payload for LLM analysis |
| `scenarios.json` | Structured scenario output |
| `scenario_scores.json` | Per-scenario priority scores and score components |
| `scheduled_tests.json` | Selected scenarios and CI batch layout |
| `ci_results.json` | Simulated or real CI outcomes |
| `failure_history.json` | Accumulated failure memory for future scoring |
| `validated_specs.json` | Cache of successful specs to suppress retesting |
| `prompt_logic.md` | Prompt design notes |
| `spack_spec.md` | Human-readable risky spec report |
| `writeup.md` | Scaling and CI strategy notes |

## Quick Start

### Summarize and Generate Scenarios

```bash
# Static mode: no Spack installation required
python3 summarize.py --force-static

# Live mode: uses Spack when available
python3 summarize.py

# Anthropic-backed generation
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python3 generate_scenarios.py --output-json scenarios.json

# Local LM Studio generation
pip install openai
python3 generate_scenarios.py \
  --lm-studio \
  --lm-studio-url "http://localhost:1234/v1" \
  --lm-studio-model "qwen2.5-coder-7b-instruct-mlx" \
  --output-json scenarios.json

# Deterministic fallback generation
python3 generate_scenarios.py --mock-only --output-json scenarios.json
```

### Run the CI-Aware Extension

```bash
# Full orchestrated run
python3 main.py --mock-only --deterministic --max-tests 5 --force-static

# Or run the downstream stages individually
python3 scoring.py scenarios.json
python3 scheduler.py --max-tests 5
python3 ci_runner.py --deterministic
python3 feedback.py
```

### Exclude Known-Good Specs

```bash
python3 generate_scenarios.py \
  --validated-spec "spack install root@6.30.06 ^libxml2@2.13.0 ^clhep@2.4.7.1 +python +roofit"

python3 scheduler.py \
  --validated-spec "spack install root@6.30.06 ^libxml2@2.13.0 ^clhep@2.4.7.1 +python +roofit"
```

## Scenario Scoring Model

`scoring.py` computes:

```text
score(spec) =
  w1 * unbounded_dependency_count +
  w2 * version_distance_from_latest +
  w3 * rarity_score +
  w4 * failure_history_weight
```

Default weights:

- `unbounded_dependency_count = 3.0`
- `version_distance_from_latest = 2.0`
- `rarity_score = 1.25`
- `failure_history_weight = 2.5`

This favors high-risk OLE candidates, middle-aged primary versions, configs not
recently explored, and neighborhoods near prior failures.

## CI-Aware Behavior

- Metadata fingerprints in `analysis_cache.json` avoid re-running downstream
  analysis when package metadata has not changed.
- `main.py` short-circuits the scheduling pipeline when `summary.json` reports
  no changed packages.
- `scheduler.py` removes duplicate fingerprints and previously validated specs.
- `feedback.py` boosts scenarios similar to recent failures using the existing
  `spec_distance()` metric, preserving the original deduplication design.

## Deduplication

`generate_scenarios.py` uses a weighted distance over:

- dependency-set Jaccard distance
- dependency-version distance
- primary-version distance
- variant-set distance

This keeps adjacent variants of the same OLE idea from crowding out the final
report while still preserving materially different dependency-risk vectors.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | bad input such as missing or malformed summary data |
| `2` | could not assemble 3 distinct scenarios after deduplication |
| `3` | `anthropic` package is missing |

## Notes

- The project works in environments without Spack installed.
- The CI extension does not require an LLM when `--mock-only` is used.
- Deterministic mode is intended for repeatable tests and qualification demos.
- The canonical repository location is [aashirvad08/spack-ai](https://github.com/aashirvad08/spack-ai).
