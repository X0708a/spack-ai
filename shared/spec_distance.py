from __future__ import annotations

import hashlib
import re
from typing import Any

VERSION_RE = re.compile(r"\d+")
SPEC_TOKEN_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*)(@[^%\s+~^]+)?")


def parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in VERSION_RE.findall(version))


def version_distance(left: str, right: str) -> float:
    left_tuple = parse_version(left)
    right_tuple = parse_version(right)
    if not left_tuple and not right_tuple:
        return 0.0
    if not left_tuple or not right_tuple:
        return 1.0
    if left_tuple == right_tuple:
        return 0.0

    max_len = max(len(left_tuple), len(right_tuple), 3)
    padded_left = left_tuple + (0,) * (max_len - len(left_tuple))
    padded_right = right_tuple + (0,) * (max_len - len(right_tuple))

    major_gap = min(abs(padded_left[0] - padded_right[0]), 1)
    minor_gap = min(abs(padded_left[1] - padded_right[1]), 5) / 5.0
    patch_gap = min(abs(padded_left[2] - padded_right[2]), 10) / 10.0
    return min(1.0, (0.6 * major_gap) + (0.25 * minor_gap) + (0.15 * patch_gap))


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - (len(left & right) / len(union))


def _dep_version_distance(left: dict[str, str], right: dict[str, str]) -> float:
    common_dependencies = sorted(set(left) & set(right))
    if not common_dependencies:
        return 0.0

    distances = [version_distance(left[name], right[name]) for name in common_dependencies]
    return sum(distances) / len(distances)


def parse_spec(spec_str: str) -> dict[str, Any]:
    spec = re.sub(r"^spack\s+install\s+", "", spec_str.strip())
    tokens = spec.split()
    if not tokens:
        raise ValueError("empty spec")

    primary_match = SPEC_TOKEN_RE.match(tokens[0])
    if not primary_match:
        raise ValueError(f"unable to parse primary package from '{spec_str}'")

    dependencies: dict[str, str] = {}
    variants: set[str] = set()
    for token in tokens[1:]:
        if token.startswith("^"):
            dep_match = SPEC_TOKEN_RE.match(token[1:])
            if dep_match:
                dependencies[dep_match.group(1)] = dep_match.group(2) or ""
        elif token.startswith("+") or token.startswith("~"):
            variants.add(token)

    return {
        "primary": primary_match.group(1),
        "version": primary_match.group(2) or "",
        "deps": dependencies,
        "variants": variants,
    }


def dependency_names(spec_str: str) -> set[str]:
    return set(parse_spec(spec_str)["deps"])


def spec_distance(spec_a: str, spec_b: str) -> float:
    parsed_a = parse_spec(spec_a)
    parsed_b = parse_spec(spec_b)

    if parsed_a["primary"] != parsed_b["primary"]:
        return 1.0

    dep_distance = _jaccard_distance(set(parsed_a["deps"]), set(parsed_b["deps"]))
    dep_version_distance = _dep_version_distance(parsed_a["deps"], parsed_b["deps"])
    variant_distance = _jaccard_distance(parsed_a["variants"], parsed_b["variants"])
    primary_distance = version_distance(parsed_a["version"], parsed_b["version"])

    score = (
        (0.40 * dep_distance)
        + (0.25 * dep_version_distance)
        + (0.20 * primary_distance)
        + (0.15 * variant_distance)
    )
    return min(score, 1.0)


def spec_fingerprint(spec: str) -> str:
    normalised = re.sub(r"\s+", " ", spec.strip().lower())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]
