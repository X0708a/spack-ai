# Spack-AI Diagnostic Bridge

Spack-AI Diagnostic Bridge is organized as a small production-style research
prototype: the evaluation pipeline lives under `eval/`, the CI-aware extension
lives under `ci_extension/`, and shared logic sits in `shared/`.

## Repository Layout

```text
spack-ai/
├── eval/
│   ├── summarize.py
│   ├── generate_scenarios.py
│   ├── summary.json
│   ├── scenarios.json
│   ├── spack_spec.md
│   ├── prompt_logic.md
│   └── README.md
├── ci_extension/
│   ├── scoring.py
│   ├── scheduler.py
│   ├── ci_runner.py
│   ├── feedback.py
│   ├── main.py
│   ├── scenario_scores.json
│   ├── scheduled_tests.json
│   ├── ci_results.json
│   ├── failure_history.json
│   ├── validated_specs.json
│   └── README.md
├── shared/
│   ├── spec_distance.py
│   ├── utils.py
│   └── config.py
├── docs/
│   ├── architecture.md
│   ├── pipeline.md
│   └── results.md
├── examples/
│   └── demo_run.md
├── README.md
└── requirements.txt
```

`eval/analysis_cache.json` is generated at runtime to support incremental
metadata-aware CI runs.

## Quick Start

```bash
pip install -r requirements.txt

python3 eval/summarize.py --force-static
python3 eval/generate_scenarios.py --mock-only --output-json eval/scenarios.json

python3 ci_extension/scoring.py
python3 ci_extension/scheduler.py --max-tests 5
python3 ci_extension/ci_runner.py --deterministic
python3 ci_extension/feedback.py

python3 ci_extension/main.py --mock-only --deterministic --max-tests 5 --force-static
```

## What Lives Where

- `eval/` contains the original qualification-task flow: metadata extraction,
  compact summary generation, prompt logic, and OLE scenario generation.
- `ci_extension/` contains the production-oriented scoring, scheduling, CI
  simulation, and feedback loop.
- `shared/` contains the spec distance metric, common path configuration, and
  JSON/logging helpers used across both halves.
- `docs/` explains the architecture, command flow, and current sample results.
- `examples/` shows a reproducible demo run from a clean checkout.

## Entry Points

- `python3 eval/summarize.py`
- `python3 eval/generate_scenarios.py --output-json eval/scenarios.json`
- `python3 ci_extension/main.py --mock-only --deterministic --max-tests 5 --force-static`

## Evaluation Task vs My Contribution

### Evaluation Task

- Metadata summarization
- LLM-based scenario generation
- Deduplication

### My Contribution

- CI-aware scheduling
- Feedback loop
- Adaptive prioritization

This keeps the original qualification task easy to identify while making the
extension work equally explicit for reviewers.

## Notes

- The repo still supports fallback mode with no Spack installation.
- The CI extension still supports no-LLM environments via `--mock-only`.
- The canonical repository location is [aashirvad08/spack-ai](https://github.com/aashirvad08/spack-ai).
