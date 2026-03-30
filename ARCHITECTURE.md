# Architecture

This repository is split into two runnable layers:

- `eval/` contains the original qualification-task pipeline
- `ci_extension/` adds CI-aware scheduling and feedback on top of that pipeline

## Data Flow

```text
Spack metadata
  -> eval/summarize.py
  -> eval/summary.json
  -> eval/generate_scenarios.py
  -> eval/scenarios.json + eval/spack_spec.md
  -> ci_extension/scoring.py
  -> ci_extension/scenario_scores.json
  -> ci_extension/scheduler.py
  -> ci_extension/scheduled_tests.json
  -> ci_extension/ci_runner.py
  -> ci_extension/ci_results.json
  -> ci_extension/feedback.py
  -> ci_extension/failure_history.json + ci_extension/validated_specs.json
```

The evaluation layer produces candidate OLE scenarios. The extension layer turns
those scenarios into a CI testing loop that can rank, batch, execute, and learn
from results.

## JSON Artifacts

- `eval/summary.json`: compact package metadata used as LLM input
- `eval/scenarios.json`: structured OLE scenarios after validation and deduplication
- `ci_extension/scenario_scores.json`: scenario scores and score components
- `ci_extension/scheduled_tests.json`: selected test set plus dependency-overlap batches
- `ci_extension/ci_results.json`: CI outcomes for scheduled scenarios
- `ci_extension/failure_history.json`: accumulated memory of failed specs
- `ci_extension/validated_specs.json`: successful specs cached to avoid redundant reruns

These artifacts are intentionally plain JSON so CI systems can cache, diff,
publish, and inspect them without custom tooling.

## Feedback Loop

The feedback loop starts after CI execution:

1. `ci_runner.py` records success or failure for each scheduled spec.
2. `feedback.py` updates failure history and validated spec caches.
3. Similar scenarios receive a score boost using the existing `spec_distance()`.
4. The next scheduling run prefers configurations near prior failures while
   avoiding already validated specs.

This keeps the system adaptive without changing the original evaluation logic.
