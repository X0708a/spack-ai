# Results

The repository includes generated artifacts from a deterministic mock run.

## Evaluation Artifacts

- `eval/summary.json`: compact metadata for `root`, `geant4`, and `clhep`
- `eval/scenarios.json`: three structured OLE-risk scenarios
- `eval/spack_spec.md`: human-readable report with spec fingerprints

## CI Extension Artifacts

- `ci_extension/scenario_scores.json`: per-scenario scores and score components
- `ci_extension/scheduled_tests.json`: ranked selections plus dependency-overlap batches
- `ci_extension/ci_results.json`: deterministic CI outcomes
- `ci_extension/failure_history.json`: recorded failure memory
- `ci_extension/validated_specs.json`: successful specs retained for suppression

## Current Demonstration State

- scoring prioritizes `root` scenarios above `geant4` because the current
  compressed metadata exposes more unbounded dependency edges in `root`
- scheduling groups the sample scenarios into one batch because they share
  `clhep`
- feedback increases future priority for scenarios that are close to prior
  failures using the same `spec_distance()` metric used for deduplication
