# Demo Run

From the repository root:

```bash
python3 eval/summarize.py --force-static
python3 eval/generate_scenarios.py --mock-only --output-json eval/scenarios.json
python3 ci_extension/main.py --mock-only --deterministic --max-tests 5 --force-static
```

Expected artifact locations:

- `eval/summary.json`
- `eval/scenarios.json`
- `eval/spack_spec.md`
- `ci_extension/scenario_scores.json`
- `ci_extension/scheduled_tests.json`
- `ci_extension/ci_results.json`
- `ci_extension/failure_history.json`
- `ci_extension/validated_specs.json`

The deterministic mode keeps the CI simulation reproducible for screenshots,
qualification review, and regression tests.
