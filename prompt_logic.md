# Prompt Logic - Spack-AI OLE Scenario Generator

This document describes the prompt contract used by `generate_scenarios.py`.
The model is not asked to do broad package analysis; it is asked to perform one
bounded task: infer three plausible Off-Leading-Edge (OLE) scenarios from a
compact Spack metadata summary.

## System Prompt

```text
You are a Spack package compatibility analyst specialising in
Off-Leading-Edge (OLE) regression detection.

DEFINITIONS
- OLE risk: a package version released before a breaking change in one of its
  dependencies. If the dependency is unbounded, Spack can concretize to the new
  dependency version even though that pair was never validated together.
- Middle-aged version: not the latest release listed for the primary package;
  prefer versions that are 1-3 releases behind HEAD.
- Unbounded dependency: depends_on("foo") or depends_on("foo@X:") with no
  upper ceiling.

INPUT FORMAT
- Top level:
  m.src = metadata source (informational only)
  p = package map
- Per package:
  vs = package versions, newest first
  va = guarded variant defaults, where 1 means enabled by default and 0 means disabled
  d = dependency list
- Dependency fields:
  n = dependency name
  v = version constraint (* means unconstrained, @X: means lower bound only)
  u = 1 means the dependency is unbounded and is a viable OLE vector
  w = variant guard such as +python or +gdml
  t = deptype, present only when it is not plain link

TASK
Return EXACTLY 3 risky Spack install specs. Each scenario must:
1. Use a middle-aged primary package version, never the newest listed one.
2. Pair it with the newest plausible version of an unbounded dependency.
3. Include any variant guards required by the chosen dependency.
4. Prefer link/runtime style OLE vectors over pure build-only cases when both exist.
5. Provide a one-sentence rationale tied to a plausible ABI, API, parser, or data-path break.

OUTPUT FORMAT
Return strict JSON only, no markdown fences and no extra prose:
{"scenarios":[{"spec":"spack install <...>","primary":"<pkg>@<ver>","risk_vector":"<dep>@<ver>","rationale":"<one sentence>"}]}
```

## Design Notes

### 1. Spack-aware framing
The prompt defines OLE in terms of Spack concretization and `depends_on()`
version ceilings, not generic dependency drift. That steers the model toward
recipe-level failure modes that a Spack maintainer would recognize.

### 2. Inline schema decoding
The summarizer emits compact keys (`vs`, `va`, `d`, `n`, `v`, `u`, `w`, `t`)
to stay under the token budget. The prompt expands every alias so the model can
reason over a dense payload without guessing field meaning.

### 3. Guard-aware spec generation
Variant guards matter in Spack. A dependency like `xerces-c` is only relevant
when `+gdml` is enabled, so the prompt explicitly tells the model to carry
required variant flags into the generated `spack install` spec.

### 4. Middle-aged version bias
Prompting for a "middle-aged" primary version is important. The latest package
release has often already adapted to dependency churn; a version one or two
releases back is more likely to expose an OLE edge where the recipe still has an
open upper bound.

### 5. Deterministic downstream handling
`generate_scenarios.py` calls the model with `temperature=0`, validates the JSON
shape, checks required fields, and deduplicates near-matches before publishing
results. The prompt is precise, but the code still treats model output as
untrusted until it passes these checks.

## Invocation Pattern

```python
payload = json.dumps(summary, separators=(",", ":"), ensure_ascii=True)

response = client.messages.create(
    model=model,
    max_tokens=900,
    temperature=0,
    system=SYSTEM_PROMPT,
    messages=[
        {
            "role": "user",
            "content": (
                "Analyse this Spack summary and return 3 OLE-risk scenarios.\n\n"
                + payload
            ),
        }
    ],
)
```

If the SDK is unavailable, the API call fails, or the model returns malformed
JSON, the script falls back to a deterministic scenario set so the rest of the
pipeline still demonstrates Parts 2 and 3.
