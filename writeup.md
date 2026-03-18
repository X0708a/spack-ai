# Spack-AI Diagnostic Bridge

The project keeps the original three-stage pipeline intact:

1. `summarize.py` extracts package metadata from `spack info --json` when Spack
   is present, or from a curated fallback dataset when it is not.
2. `generate_scenarios.py` feeds a compressed summary into an LLM to propose
   OLE-risk install specs.
3. A weighted similarity pass removes redundant scenarios before reporting.

## Review Summary

The architecture was already appropriate for a qualification task, so the work
here focused on making it look like a production-quality research prototype:

- normalized live Spack JSON and fallback data into one internal schema
- replaced advisory token logging with an actual compression-budget loop
- kept the OLE signal intact while shrinking the LLM payload with short keys and
  guard-focused variant summaries
- moved deduplication into the final output path instead of treating it as a
  side demo
- added output validation, deterministic fallback behavior, and clearer CLI
  controls for reproducible runs

## Why This Is Spack-Specific

This prototype is grounded in how Spack recipes encode compatibility:

- unbounded dependencies are detected from open-ended constraints such as
  `depends_on("foo")` and `depends_on("foo@X:")`
- conditional dependencies preserve `when=` guards so generated specs include
  required toggles like `+gdml` or `+python`
- scenario selection intentionally targets middle-aged package versions, because
  they are the most likely to lag behind upstream ABI or parser changes while
  still satisfying broad concretizer constraints

That is the right abstraction level for OLE analysis: recipe metadata first,
then LLM-guided hypothesis generation, then deterministic pruning.

## CI and Automation Story

The project is easy to wire into CI:

- run `python3 summarize.py --force-static` as a smoke path on machines without Spack
- run `python3 summarize.py` on runners that have a Spack checkout
- generate scenarios with `python3 generate_scenarios.py --validated-spec ...`
  to suppress already reviewed specs
- publish `summary.json` and `spack_spec.md` as CI artifacts for reviewer inspection
- key cache entries by normalized spec fingerprint so only novel OLE candidates
  trigger human review

This keeps the system useful both as a local diagnostic tool and as a scheduled
regression sentinel.

The pipeline also uses metadata fingerprint caching so only packages whose
dependency metadata changes are re-summarized and re-sent to the LLM in CI.

## Scaling Strategy

At large stack sizes, the bottleneck is not metadata extraction but prompt
bandwidth. The practical scaling approach is to make prompt cost proportional to
change, not to total environment size.

First, keep raw extraction separate from LLM input generation. `summarize.py`
can normalize full package metadata once, then emit an OLE-focused view that
retains only the fields needed for risk reasoning: recent package versions,
guarded variants, and compact dependency directives. Second, partition the
package DAG into connected risk regions based on shared unbounded dependencies.
That supports map-reduce style prompting: analyze each region independently, then
merge only the resulting risk candidates. Third, rank packages before sending
them to the model. A simple score such as
`unbounded_link_or_run_deps * version_age * guard_fanout` pushes the most likely
OLE surfaces to the front of the queue. Finally, cache normalized spec
fingerprints and re-run the LLM only when a recipe, dependency bound, or
selected package version changes.

The result is near-linear work in the changed slice of the stack rather than in
the absolute number of packages, which makes hundred-package environments
tractable and thousand-package environments realistic with batching.
