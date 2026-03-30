# Pipeline

## Evaluation Flow

```text
Spack metadata
  -> eval/summarize.py
  -> eval/summary.json
  -> eval/generate_scenarios.py
  -> eval/scenarios.json + eval/spack_spec.md
```

## CI Extension Flow

```text
eval/scenarios.json
  -> ci_extension/scoring.py
  -> ci_extension/scenario_scores.json
  -> ci_extension/scheduler.py
  -> ci_extension/scheduled_tests.json
  -> ci_extension/ci_runner.py
  -> ci_extension/ci_results.json
  -> ci_extension/feedback.py
  -> ci_extension/failure_history.json + ci_extension/validated_specs.json
```

## End-to-End Command

```bash
python3 ci_extension/main.py --mock-only --deterministic --max-tests 5 --force-static
```

This command:

1. runs `eval/summarize.py`
2. runs `eval/generate_scenarios.py`
3. scores scenarios
4. schedules the highest-value tests
5. simulates CI
6. updates feedback artifacts

If no metadata changed, the orchestration step exits early after summarization.
