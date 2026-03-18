#!/usr/bin/env python3
"""
summarize.py - GSoC 2026 Spack-AI Diagnostic Bridge
Part 1: Spack metadata extraction and token-bounded summarization.

The script prefers live `spack info --json` data when Spack is available and
falls back to a curated static dataset otherwise. The emitted JSON is
intentionally compact because it is consumed by the OLE risk prompt in
`generate_scenarios.py`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PACKAGES = ("root", "geant4", "clhep")
DEFAULT_OUTPUT = Path("summary.json")
DEFAULT_CACHE_PATH = Path("analysis_cache.json")
DEFAULT_TOKEN_BUDGET = 500

WHEN_VARIANT_RE = re.compile(r"(?<![\w-])([+~])([A-Za-z0-9_][A-Za-z0-9_-]*)")

# Curated fallback data used when Spack is unavailable. The values are kept
# close to real package.py directives so the rest of the pipeline remains useful
# in bare environments such as coding challenge runners or CI smoke tests.
FALLBACK_DATA: dict[str, Any] = {
    "root": {
        "versions": ["6.32.02", "6.30.06", "6.28.12", "6.26.10"],
        "variants": {
            "python": {"default": True, "description": "Enable Python support"},
            "tmva": {"default": True, "description": "Build TMVA subpackage"},
            "roofit": {"default": True, "description": "Build RooFit subpackage"},
            "minuit": {"default": True, "description": "Build Minuit minimizer"},
            "opengl": {"default": False, "description": "Enable OpenGL support"},
            "x": {"default": False, "description": "Enable X11 support"},
        },
        "dependencies": [
            {"name": "cmake", "type": ["build"], "when": None, "version": "@3.16:"},
            {"name": "python", "type": ["build", "link"], "when": "+python", "version": "@3.8:"},
            {"name": "libxml2", "type": ["link"], "when": None, "version": None},
            {"name": "xz", "type": ["link"], "when": None, "version": None},
            {"name": "pcre", "type": ["link"], "when": None, "version": "@8.35:"},
            {"name": "zlib-ng", "type": ["link"], "when": None, "version": "@2.1:"},
            {"name": "lz4", "type": ["link"], "when": None, "version": "@1.7:"},
            {"name": "zstd", "type": ["link"], "when": None, "version": None},
            {"name": "openssl", "type": ["link"], "when": None, "version": "@1.0.2:"},
            {"name": "freetype", "type": ["link"], "when": None, "version": None},
            {"name": "glew", "type": ["link"], "when": "+opengl", "version": None},
            {"name": "gl", "type": ["link"], "when": "+opengl", "version": None},
            {"name": "clhep", "type": ["link"], "when": None, "version": "@2.4.1.0:"},
            {"name": "xxhash", "type": ["link"], "when": None, "version": "@0.6.5:"},
        ],
    },
    "geant4": {
        "versions": ["11.2.1", "11.1.3", "11.0.4", "10.7.4"],
        "variants": {
            "qt": {"default": False, "description": "Enable Qt visualization"},
            "opengl": {"default": False, "description": "Enable OpenGL visualization"},
            "python": {"default": False, "description": "Build Geant4 Python bindings"},
            "gdml": {"default": True, "description": "Enable GDML geometry"},
            "vecgeom": {"default": False, "description": "Use VecGeom geometry library"},
            "hdf5": {"default": False, "description": "Build with HDF5 support"},
            "geant4_data": {"default": True, "description": "Install Geant4 data sets"},
        },
        "dependencies": [
            {"name": "cmake", "type": ["build"], "when": None, "version": "@3.16:"},
            {"name": "clhep", "type": ["link"], "when": None, "version": "@2.4.6.0:"},
            {"name": "expat", "type": ["link"], "when": "+gdml", "version": None},
            {"name": "libxml2", "type": ["link"], "when": "+gdml", "version": None},
            {"name": "xerces-c", "type": ["link"], "when": "+gdml", "version": "@3.2:"},
            {"name": "zlib-ng", "type": ["link"], "when": None, "version": None},
            {"name": "qt", "type": ["link"], "when": "+qt", "version": "@5.6:"},
            {"name": "gl", "type": ["link"], "when": "+opengl", "version": None},
            {"name": "vecgeom", "type": ["link"], "when": "+vecgeom", "version": "@1.2.0:"},
            {"name": "hdf5", "type": ["link"], "when": "+hdf5", "version": "@1.10:"},
            {"name": "python", "type": ["link"], "when": "+python", "version": "@3.6:"},
            {"name": "boost", "type": ["link"], "when": "+python", "version": None},
        ],
    },
    "clhep": {
        "versions": ["2.4.7.1", "2.4.6.4", "2.4.5.4", "2.4.4.2"],
        "variants": {
            "cxx17": {"default": True, "description": "Enable C++17 support"},
            "shared": {"default": True, "description": "Build shared libraries"},
        },
        "dependencies": [
            {"name": "cmake", "type": ["build"], "when": None, "version": "@3.12:"},
        ],
    },
}


@dataclass(frozen=True)
class CompressionProfile:
    name: str
    max_versions: int = 4
    guard_variants_only: bool = True
    omit_build_only: bool = False


@dataclass(frozen=True)
class SummaryBuildResult:
    summary: dict[str, Any]
    token_count: int
    token_method: str
    profile: CompressionProfile
    source: str
    missing_packages: tuple[str, ...]
    changed_packages: tuple[str, ...]
    unchanged_packages: tuple[str, ...]
    fingerprints: dict[str, str]


PROFILES = (
    CompressionProfile(name="guard-variants"),
    CompressionProfile(name="guard-variants-no-build", omit_build_only=True),
    CompressionProfile(name="guard-variants-no-build-top3", max_versions=3, omit_build_only=True),
    CompressionProfile(name="guard-variants-no-build-top2", max_versions=2, omit_build_only=True),
)


def info(message: str) -> None:
    print(f"[info] {message}", file=sys.stderr)


def warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Spack metadata and emit a token-bounded summary for OLE analysis.",
    )
    parser.add_argument(
        "packages",
        nargs="*",
        default=list(DEFAULT_PACKAGES),
        help="Target package names (defaults to root geant4 clhep).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write the compact JSON summary.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help="Maximum token budget for the compact JSON payload.",
    )
    parser.add_argument(
        "--force-static",
        action="store_true",
        help="Skip live Spack detection and use the curated fallback dataset.",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="Fingerprint cache used to skip unchanged package analysis.",
    )
    return parser.parse_args()


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        warn(f"timed out while running: {' '.join(command)}")
        return None


def _spack_available() -> bool:
    if shutil.which("spack") is None:
        return False
    result = _run_command(["spack", "--version"], timeout=10)
    return bool(result and result.returncode == 0)


def _spack_info(package: str) -> dict[str, Any]:
    result = _run_command(["spack", "info", "--json", package], timeout=30)
    if result is None or result.returncode != 0:
        return {}

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        warn(f"received invalid JSON from 'spack info --json {package}'")
        return {}

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and item.get("name") == package:
                return item
        if len(payload) == 1 and isinstance(payload[0], dict):
            return payload[0]
        return {}

    return payload if isinstance(payload, dict) else {}


def _extract_versions(raw: dict[str, Any]) -> list[str]:
    versions = raw.get("versions", [])

    if isinstance(versions, dict):
        ordered = list(versions.keys())
    else:
        ordered = []
        for item in versions:
            if isinstance(item, dict):
                value = item.get("version")
            else:
                value = item
            if value:
                ordered.append(str(value))

    seen: set[str] = set()
    unique: list[str] = []
    for version in ordered:
        if version not in seen:
            seen.add(version)
            unique.append(version)
    return unique


def _extract_variants(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    variants = raw.get("variants", {})
    normalized: dict[str, dict[str, Any]] = {}

    if isinstance(variants, dict):
        items = variants.items()
    elif isinstance(variants, list):
        items = []
        for item in variants:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                items.append((str(name), item))
    else:
        items = []

    for name, payload in items:
        default = payload.get("default")
        normalized[name] = {
            "default": bool(default) if isinstance(default, bool) else default,
            "description": payload.get("description", ""),
        }

    return normalized


def _normalize_deptypes(dep: dict[str, Any]) -> list[str]:
    raw_types = dep.get("deptype") or dep.get("type") or ["link"]
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    return sorted({str(item) for item in raw_types if item})


def _extract_dependencies(raw: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies = raw.get("dependencies", [])
    if isinstance(dependencies, dict):
        iterable = dependencies.values()
    else:
        iterable = dependencies

    normalized: list[dict[str, Any]] = []
    for dep in iterable:
        if not isinstance(dep, dict):
            continue
        name = dep.get("name")
        if not name:
            continue
        normalized.append(
            {
                "name": str(name),
                "type": _normalize_deptypes(dep),
                "when": dep.get("when") or None,
                "version": dep.get("version") or None,
            }
        )
    return normalized


def _extract_live(package: str) -> dict[str, Any]:
    raw = _spack_info(package)
    if not raw:
        return {}
    return {
        "versions": _extract_versions(raw),
        "variants": _extract_variants(raw),
        "dependencies": _extract_dependencies(raw),
    }


def _is_unbounded(version: str | None) -> bool:
    if version is None:
        return True

    version = version.strip()
    if not version or version == "*":
        return True

    clauses = [clause.strip() for clause in version.split(",") if clause.strip()]
    if not clauses:
        return True

    for clause in clauses:
        if not clause.startswith("@"):
            continue
        body = clause[1:]
        if ":" not in body:
            continue
        lower, upper = body.split(":", 1)
        if lower and not upper:
            return True
    return False


def _guard_variants(dependencies: list[dict[str, Any]]) -> set[str]:
    variants: set[str] = set()
    for dep in dependencies:
        when_clause = dep.get("when")
        if not when_clause:
            continue
        for _, variant in WHEN_VARIANT_RE.findall(str(when_clause)):
            variants.add(variant)
    return variants


def _load_package_data(package: str, use_live: bool) -> dict[str, Any]:
    if use_live:
        live_data = _extract_live(package)
        if live_data:
            return live_data
        warn(f"live extraction failed for '{package}', falling back to curated data")
    return FALLBACK_DATA.get(package, {})


def _normalize_package_data(data: dict[str, Any]) -> dict[str, Any]:
    versions = [str(version) for version in data.get("versions", []) if version]

    variants = {
        str(name): {
            "default": payload.get("default"),
            "description": payload.get("description", ""),
        }
        for name, payload in sorted(data.get("variants", {}).items())
    }

    dependencies = [
        {
            "name": str(dep["name"]),
            "type": sorted(str(item) for item in dep.get("type", ["link"]) if item),
            "when": dep.get("when") or None,
            "version": dep.get("version") or None,
        }
        for dep in sorted(
            data.get("dependencies", []),
            key=lambda item: (
                item.get("name", ""),
                item.get("when") or "",
                item.get("version") or "",
                ",".join(item.get("type", [])),
            ),
        )
        if dep.get("name")
    ]

    return {
        "versions": versions,
        "variants": variants,
        "dependencies": dependencies,
    }


def metadata_fingerprint(metadata: dict[str, Any]) -> str:
    """Stable fingerprint for the normalized package metadata."""
    payload = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def load_analysis_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        warn(f"unable to read cache file {path}; starting with an empty cache")
        return {}

    if not isinstance(raw, dict):
        warn(f"cache file {path} is not a JSON object; starting with an empty cache")
        return {}

    cache: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            cache[key] = value
    return cache


def write_analysis_cache(path: Path, existing_cache: dict[str, str], fingerprints: dict[str, str]) -> None:
    cache = dict(existing_cache)
    cache.update(fingerprints)
    path.write_text(
        json.dumps(dict(sorted(cache.items())), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _compress_package(data: dict[str, Any], profile: CompressionProfile) -> dict[str, Any]:
    dependencies = data.get("dependencies", [])
    guard_variants = _guard_variants(dependencies)

    compressed_deps: list[dict[str, Any]] = []
    for dep in sorted(
        dependencies,
        key=lambda item: (
            item.get("name", ""),
            item.get("when") or "",
            item.get("version") or "",
            ",".join(item.get("type", [])),
        ),
    ):
        dep_types = dep.get("type", ["link"])
        if profile.omit_build_only and set(dep_types) == {"build"}:
            continue

        entry: dict[str, Any] = {
            "n": dep["name"],
            "v": dep.get("version") or "*",
        }
        if dep.get("when"):
            entry["w"] = dep["when"]
        if dep_types != ["link"]:
            entry["t"] = dep_types
        if _is_unbounded(dep.get("version")):
            entry["u"] = 1
        compressed_deps.append(entry)

    variant_items = data.get("variants", {})
    if profile.guard_variants_only:
        variant_names = sorted(name for name in guard_variants if name in variant_items)
    else:
        variant_names = sorted(variant_items)

    package_summary: dict[str, Any] = {
        "vs": data.get("versions", [])[: profile.max_versions],
        "d": compressed_deps,
    }
    if variant_names:
        package_summary["va"] = {
            name: 1 if variant_items[name].get("default") else 0
            for name in variant_names
        }
    return package_summary


def _render_summary(summary: dict[str, Any]) -> str:
    return json.dumps(summary, separators=(",", ":"), ensure_ascii=True)


def estimate_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken

        encoder = tiktoken.get_encoding("cl100k_base")
        return len(encoder.encode(text)), "tiktoken/cl100k_base"
    except Exception:
        # JSON with short keys tends to tokenize more densely than prose, so use
        # a conservative fallback ratio when the exact tokenizer is unavailable.
        return math.ceil(len(text) / 3.0), "heuristic(3.0 chars/token)"


def build_summary(
    packages: list[str],
    max_tokens: int = DEFAULT_TOKEN_BUDGET,
    force_static: bool = False,
    fingerprint_cache: dict[str, str] | None = None,
) -> SummaryBuildResult:
    use_live = not force_static and _spack_available()
    source = "spack-live" if use_live else "static-fallback"
    fingerprint_cache = fingerprint_cache or {}

    loaded: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    fingerprints: dict[str, str] = {}
    changed_packages: list[str] = []
    unchanged_packages: list[str] = []
    for package in packages:
        package_data = _load_package_data(package, use_live=use_live)
        if package_data:
            normalized_data = _normalize_package_data(package_data)
            loaded[package] = normalized_data
            fingerprint = metadata_fingerprint(normalized_data)
            fingerprints[package] = fingerprint

            if fingerprint_cache.get(package) == fingerprint:
                unchanged_packages.append(package)
                info(f"{package} unchanged -> skipping analysis")
            else:
                changed_packages.append(package)
                info(f"{package} changed -> scheduling analysis")
        else:
            missing.append(package)
            warn(f"no data available for package '{package}'")

    if not loaded:
        raise ValueError("no package metadata could be loaded")

    fallback_result: SummaryBuildResult | None = None
    for profile in PROFILES:
        summary = {
            "m": {"src": source, "changed": changed_packages},
            "p": {
                package: _compress_package(loaded[package], profile)
                for package in loaded
            },
        }
        rendered = _render_summary(summary)
        token_count, token_method = estimate_tokens(rendered)
        result = SummaryBuildResult(
            summary=summary,
            token_count=token_count,
            token_method=token_method,
            profile=profile,
            source=source,
            missing_packages=tuple(missing),
            changed_packages=tuple(changed_packages),
            unchanged_packages=tuple(unchanged_packages),
            fingerprints=fingerprints,
        )
        if token_count <= max_tokens:
            return result
        fallback_result = result

    assert fallback_result is not None
    return fallback_result


def main() -> None:
    args = parse_args()
    packages = dedupe_preserve_order(list(args.packages))
    info(f"extracting metadata for: {', '.join(packages)}")
    fingerprint_cache = load_analysis_cache(args.cache_file)

    try:
        result = build_summary(
            packages=packages,
            max_tokens=args.max_tokens,
            force_static=args.force_static,
            fingerprint_cache=fingerprint_cache,
        )
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)

    output = _render_summary(result.summary)
    if result.token_count > args.max_tokens:
        print(
            "[error] unable to fit the summary within the requested token budget "
            f"after applying all compression profiles ({result.token_count} tokens).",
            file=sys.stderr,
        )
        sys.exit(2)

    args.output.write_text(output + "\n", encoding="utf-8")
    write_analysis_cache(args.cache_file, fingerprint_cache, result.fingerprints)
    info(
        "written "
        f"{args.output} using {result.source}, profile={result.profile.name}, "
        f"tokens={result.token_count} via {result.token_method}"
    )
    info(f"updated metadata cache at {args.cache_file}")
    if result.missing_packages:
        warn(f"missing packages omitted from summary: {', '.join(result.missing_packages)}")

    print(output)


if __name__ == "__main__":
    main()
