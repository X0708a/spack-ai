# CI Extension

This directory extends the evaluation pipeline into a CI-aware testing module.

## Files

- `scoring.py`: scores OLE scenarios by risk, age, rarity, and failure history
- `scheduler.py`: selects and batches the best scenarios for the next CI run
- `ci_runner.py`: simulates or optionally concretizes scheduled specs
- `feedback.py`: updates failure history and validated-spec caches
- `main.py`: orchestrates summarize, generation, scoring, scheduling, CI, and feedback

## Artifacts

- `scenario_scores.json`
- `scheduled_tests.json`
- `ci_results.json`
- `failure_history.json`
- `validated_specs.json`

## Commands

```bash
python3 ci_extension/scoring.py
python3 ci_extension/scheduler.py --max-tests 5
python3 ci_extension/ci_runner.py --deterministic
python3 ci_extension/feedback.py

python3 ci_extension/main.py --mock-only --deterministic --max-tests 5 --force-static
```
