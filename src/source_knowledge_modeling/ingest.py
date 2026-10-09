"""Local source adapters and generation of an explicitly unreviewed v1 artifact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Protocol

from .core import KnowledgeFoundationError, SCHEMA_VERSION, digest_bytes, digest_text, validate


@dataclass(frozen=True)
class ExtractedUnit:
    locator: str
    kind: str
    text: str
    bbox: list[float] | None = None
    visual_ref: str | None = None
    raw_content: Any = None
    continues_prev: bool = False


class SourceAdapter(Protocol):
    name: str

    def extract(self, source: Path, *, pages: str | None = None) -> tuple[list[ExtractedUnit], dict[str, Any]]: ...


class TextAdapter:
    name = "direct-text"

    def extract(self, source: Path, *, pages: str | None = None) -> tuple[list[ExtractedUnit], dict[str, Any]]:
        if pages is not None:
            raise KnowledgeFoundationError("Page selection applies only to PDF input.")
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise KnowledgeFoundationError(f"Cannot read UTF-8 text source: {source}") from exc
        if not lines:
            raise KnowledgeFoundationError("Text source has no lines to inventory.")
        return [ExtractedUnit(f"line:{index}", "blank" if not line else "text", line) for index, line in enumerate(lines, 1)], {"name": self.name, "version": "1", "privacy": "local"}


def _text_from_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for child in value if (part := _text_from_content(child)))
    if isinstance(value, dict):
        return _text_from_content(value.get("content"))
    return ""


def _page_numbers(spec: str) -> list[int]:
    try:
        parts = spec.split("-")
        start, end = (int(parts[0]), int(parts[-1]))
        if len(parts) not in (1, 2) or start < 1 or end < start:
            raise ValueError
    except ValueError as exc:
        raise KnowledgeFoundationError("PDF pages must be a positive page or inclusive start-end range.") from exc
    return list(range(start, end + 1))


def units_from_mineru_middle(middle: dict[str, Any], *, pages: str | None = None) -> list[ExtractedUnit]:
    """Normalize MinerU 4 middle JSON while retaining each visual block."""
    middle_pages = middle.get("pages")
    if not isinstance(middle_pages, list) or not middle_pages:
        raise KnowledgeFoundationError("MinerU middle JSON has no pages.")
    result: list[ExtractedUnit] = []
    seen: set[str] = set()
    requested = set(_page_numbers(pages)) if pages else None
    selected = [page for page in middle_pages if isinstance(page, dict) and (requested is None or (isinstance(page.get("page_idx"), int) and page["page_idx"] + 1 in requested))]
    if requested is not None and {page["page_idx"] + 1 for page in selected} != requested:
        raise KnowledgeFoundationError("MinerU middle JSON does not cover every requested PDF page.")
    page_indices: set[int] = set()
    for page in selected:
        page_idx = page.get("page_idx") if isinstance(page, dict) else None
        if not isinstance(page_idx, int) or page_idx < 0:
            raise KnowledgeFoundationError("MinerU page lacks a valid page index.")
        if page_idx in page_indices:
            raise KnowledgeFoundationError(f"Duplicate MinerU page index: {page_idx}.")
        page_indices.add(page_idx)
        pdf_page = page_idx + 1
        blocks = page.get("blocks", [])
        if not isinstance(blocks, list):
            raise KnowledgeFoundationError("MinerU page blocks must be a list.")
        if not blocks:
            result.append(ExtractedUnit(f"pdf-page:{pdf_page}/empty", "page_empty", ""))
        for block in blocks:
            index = block.get("index") if isinstance(block, dict) else None
            kind = block.get("type") if isinstance(block, dict) else None
            if not isinstance(index, int) or index < 0 or not isinstance(kind, str) or not kind:
                raise KnowledgeFoundationError(f"Invalid block on PDF page {pdf_page}.")
            locator = f"pdf-page:{pdf_page}/block:{index}"
            if locator in seen:
                raise KnowledgeFoundationError(f"Duplicate PDF block locator: {locator}")
            seen.add(locator)
            bbox = block.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(value, (int, float)) for value in bbox):
                raise KnowledgeFoundationError(f"Invalid bounding box at {locator}.")
            result.append(ExtractedUnit(locator, kind, _text_from_content(block.get("content")), [float(value) for value in bbox], f"pdf-page:{pdf_page}" if kind in ("image", "table", "chart", "equation") else None, block.get("content"), bool(block.get("continues_prev"))))
    return result


def _cache_page_numbers(path: Path) -> tuple[set[int], int] | None:
    """Read the physical PDF page set and revision stamp from a MinerU cache name."""
    match = re.fullmatch(r"(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)_(\d+)\.json", path.name)
    if match is None:
        return None
    try:
        numbers = [number for part in match.group(1).split(",") for number in _page_numbers(part)]
    except KnowledgeFoundationError:
        return None
    if len(numbers) != len(set(numbers)):
        return None
    return set(numbers), int(match.group(2))


def _local_cache_middle(cache: Path, *, pages: str, source_hash: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Select a complete set of current cache revisions for the requested pages."""
    requested = _page_numbers(pages)
    requested_set = set(requested)
    revisions: dict[int, tuple[tuple[int, int, str], Path]] = {}
    for path in cache.glob("*.json"):
        parsed_name = _cache_page_numbers(path)
        if parsed_name is None:
            continue
        covered, stamp = parsed_name
        if not covered.intersection(requested_set):
            continue
        if path.resolve().parent != cache.resolve():
            raise KnowledgeFoundationError("MinerU cache JSON must remain inside the source-hash cache directory.")
        rank = (stamp, path.stat().st_mtime_ns, path.name)
        for number in covered.intersection(requested_set):
            if number not in revisions or rank > revisions[number][0]:
                revisions[number] = (rank, path)
    missing = sorted(requested_set - revisions.keys())
    if missing:
        raise KnowledgeFoundationError(f"Local MinerU cache does not cover requested PDF pages: {missing}.")

    selected_paths = sorted({path for _, path in revisions.values()}, key=lambda path: path.name)
    loaded: dict[Path, dict[int, dict[str, Any]]] = {}
    fingerprints: list[dict[str, str]] = []
    versions: set[str] = set()
    for path in selected_paths:
        named = _cache_page_numbers(path)
        assert named is not None
        try:
            raw = path.read_bytes()
            middle = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeFoundationError(f"Cannot read MinerU cache JSON: {path}") from exc
        if not isinstance(middle, dict):
            raise KnowledgeFoundationError(f"MinerU cache JSON is not an object: {path}")
        metadata = middle.get("metadata")
        producer = metadata.get("producer") if isinstance(metadata, dict) else None
        extensions = middle.get("extensions")
        mineru = extensions.get("mineru") if isinstance(extensions, dict) else None
        if not isinstance(producer, dict) or producer.get("name") != "mineru":
            raise KnowledgeFoundationError(f"MinerU cache producer identity mismatch: {path}")
        if not isinstance(mineru, dict) or mineru.get("tier") != "standard":
            raise KnowledgeFoundationError(f"MinerU cache tier identity mismatch: {path}")
        for identity in (metadata, mineru):
            if identity.get("source_sha256", source_hash) != source_hash:
                raise KnowledgeFoundationError(f"MinerU cache source identity mismatch: {path}")
            if identity.get("privacy", "local") != "local":
                raise KnowledgeFoundationError(f"MinerU cache privacy identity mismatch: {path}")
        middle_pages = middle.get("pages")
        if not isinstance(middle_pages, list):
            raise KnowledgeFoundationError(f"MinerU cache has no page list: {path}")
        by_number: dict[int, dict[str, Any]] = {}
        for page in middle_pages:
            index = page.get("page_idx") if isinstance(page, dict) else None
            if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index + 1 in by_number:
                raise KnowledgeFoundationError(f"MinerU cache has an invalid or duplicate physical page index: {path}")
            by_number[index + 1] = page
        if set(by_number) != named[0]:
            raise KnowledgeFoundationError(f"MinerU cache filename and JSON page coverage differ: {path}")
        loaded[path] = by_number
        versions.add(str(producer.get("version", "unknown")))
        fingerprints.append({"cache_file": path.name, "sha256": digest_bytes(raw)})
    if len(versions) != 1:
        raise KnowledgeFoundationError("Selected MinerU cache revisions have different producer versions.")
    ordered_pages = [loaded[revisions[number][1]][number] for number in requested]
    return {"metadata": {"producer": {"name": "mineru", "version": versions.pop()}}, "pages": ordered_pages}, fingerprints


class MineruPdfAdapter:
    name = "mineru-local-pdf"

    def __init__(self, *, executable: Path | None = None, home: Path | None = None, middle_json: Path | None = None, middle_sha256: str | None = None, middle_source_sha256: str | None = None, middle_tier: str | None = None) -> None:
        self.executable = executable
        self.home = home
        self.middle_json = middle_json
        self.middle_sha256 = middle_sha256
        self.middle_source_sha256 = middle_source_sha256
        self.middle_tier = middle_tier

    def _run_json(self, args: list[str]) -> dict[str, Any]:
        if self.executable is None or self.home is None:
            raise KnowledgeFoundationError("Active MinerU parsing needs a CLI and local home.")
        env = {**os.environ, "MINERU_HOME": str(self.home)}
        try:
            completed = subprocess.run([str(self.executable), *args], capture_output=True, text=True, env=env, check=False)
            value = json.loads(completed.stdout)
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeFoundationError(f"MinerU command failed: {' '.join(args)}") from exc
        if not isinstance(value, dict) or "error" in value:
            raise KnowledgeFoundationError(f"MinerU returned an error: {value.get('error') if isinstance(value, dict) else value}")
        if completed.returncode and value.get("parse", {}).get("status") not in ("pending", "running"):
            raise KnowledgeFoundationError(f"MinerU failed with exit code {completed.returncode}.")
        return value

    def extract(self, source: Path, *, pages: str | None = None) -> tuple[list[ExtractedUnit], dict[str, Any]]:
        if source.suffix.lower() != ".pdf":
            raise KnowledgeFoundationError("MinerU PDF adapter requires a .pdf source.")
        if not pages:
            raise KnowledgeFoundationError("PDF ingestion requires an explicit --pages range to make the extraction scope reviewable.")
        tier = "standard"
        if self.middle_json is None:
            if self.home is None:
                raise KnowledgeFoundationError("Active MinerU parsing needs a local home.")
            status = self._run_json(["server", "status", "--json"])
            local = status.get("parse_server", {}).get("local", {})
            if not local.get("healthy") or "standard" not in local.get("supported_tiers", []):
                raise KnowledgeFoundationError("Local MinerU standard parser is unavailable; remote parsing is disabled.")
            command = ["parse", str(source), "--pages", pages, "--tier", "standard", "--wait", "240", "--json"]
            for _ in range(3):
                parsed = self._run_json(command)
                summary = parsed.get("parse", {})
                if summary.get("status") == "done":
                    break
                if summary.get("status") not in ("pending", "running"):
                    raise KnowledgeFoundationError(f"MinerU did not finish locally: {summary}")
            else:
                raise KnowledgeFoundationError("MinerU parse still running; retry the same request later.")
            source_hash = digest_bytes(source.read_bytes())
            if summary.get("sha256") != source_hash or summary.get("tier") != "standard":
                raise KnowledgeFoundationError("MinerU parse identity differs from the requested local source.")
            page_range = summary.get("page_range")
            if not isinstance(page_range, str) or not page_range:
                raise KnowledgeFoundationError("MinerU omitted its parsed page range.")
            if set(_page_numbers(page_range)) != set(_page_numbers(pages)):
                raise KnowledgeFoundationError("MinerU parsed page range differs from the requested PDF pages.")
            if summary.get("privacy", "local") != "local":
                raise KnowledgeFoundationError("MinerU parse privacy differs from the requested local parser.")
            cache = self.home / "doclib/parsed" / source_hash[:2] / source_hash / "standard"
            middle, fingerprints = _local_cache_middle(cache, pages=pages, source_hash=source_hash)
        else:
            source_hash = digest_bytes(source.read_bytes())
            expected_cache = (self.home / "doclib/parsed" / source_hash[:2] / source_hash / "standard").resolve() if self.home else None
            middle_path = self.middle_json.resolve()
            in_cache = middle_path.parent == expected_cache and middle_path.name.startswith(f"{pages}_")
            if not in_cache:
                if self.middle_source_sha256 != source_hash or not self.middle_sha256 or self.middle_tier not in ("basic", "standard", "advanced"):
                    raise KnowledgeFoundationError("External MinerU JSON needs matching --middle-source-sha256, --middle-sha256 and --middle-tier.")
                tier = self.middle_tier
        if self.middle_json is not None:
            try:
                middle_bytes = middle_path.read_bytes()
                if self.middle_sha256 and digest_bytes(middle_bytes) != self.middle_sha256:
                    raise KnowledgeFoundationError("MinerU middle JSON hash mismatch.")
                middle = json.loads(middle_bytes)
            except (OSError, json.JSONDecodeError) as exc:
                raise KnowledgeFoundationError(f"Cannot read MinerU middle JSON: {middle_path}") from exc
            fingerprints = [{"cache_file": middle_path.name, "sha256": digest_bytes(middle_bytes)}]
        if middle.get("metadata", {}).get("producer", {}).get("name") != "mineru":
            raise KnowledgeFoundationError("Middle JSON does not identify MinerU as its producer.")
        units = units_from_mineru_middle(middle, pages=pages)
        metadata = middle.get("metadata", {}).get("producer", {})
        aggregate = fingerprints[0]["sha256"] if len(fingerprints) == 1 else digest_text(json.dumps(fingerprints, sort_keys=True, separators=(",", ":")))
        return units, {"name": self.name, "version": str(metadata.get("version", "unknown")), "privacy": "local", "tier": tier, "page_range": pages, "input_sha256": aggregate, "input_files": fingerprints}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def apply_reading_order(extracted: list[ExtractedUnit], overrides: dict[str, Any]) -> list[ExtractedUnit]:
    """Apply explicit, complete per-page corrections without hiding parser blocks."""
    if overrides.get("schema_version") != "knowledge-reading-order/v1" or not isinstance(overrides.get("pages"), dict):
        raise KnowledgeFoundationError("Invalid reading-order override format.")
    by_page: dict[str, list[ExtractedUnit]] = {}
    for unit in extracted:
        if not unit.locator.startswith("pdf-page:"):
            raise KnowledgeFoundationError("Reading-order overrides apply only to PDF units.")
        page = unit.locator.split("/")[0].split(":")[1]
        by_page.setdefault(page, []).append(unit)
    for page, locators in overrides["pages"].items():
        current = by_page.get(page)
        if current is None or not isinstance(locators, list) or len(locators) != len(current) or set(locators) != {unit.locator for unit in current}:
            raise KnowledgeFoundationError(f"Reading-order override must list every unit exactly once on PDF page {page}.")
        lookup = {unit.locator: unit for unit in current}
        by_page[page] = [lookup[locator] for locator in locators]
    ordered = [unit for page_units in by_page.values() for unit in page_units]
    positions = {unit.locator: index for index, unit in enumerate(ordered)}
    continuations = overrides.get("continuations", {})
    if not isinstance(continuations, dict):
        raise KnowledgeFoundationError("Reading-order continuations must be an object.")
    for target, predecessor in continuations.items():
        if target not in positions or predecessor not in positions or positions[predecessor] >= positions[target]:
            raise KnowledgeFoundationError(f"Invalid continuation override: {target}.")
    return ordered


def ingest(source: Path, *, root: Path, workspace: Path, adapter: SourceAdapter, pages: str | None = None, order_overrides: Path | None = None) -> tuple[Path, Path]:
    """Write a new immutable extraction run; no semantic claims are inferred."""
    source, root, workspace = source.resolve(), root.resolve(), workspace.resolve()
    try:
        source_rel = source.relative_to(root)
        workspace_rel = workspace.relative_to(root)
    except ValueError as exc:
        raise KnowledgeFoundationError("Source and workspace must be inside the caller root.") from exc
    if not source.is_file():
        raise KnowledgeFoundationError(f"Source file unavailable: {source}")
    source_hash = digest_bytes(source.read_bytes())
    extracted, parser = adapter.extract(source, pages=pages)
    if not extracted:
        raise KnowledgeFoundationError("Adapter returned no content units.")
    continuations: dict[str, str] = {}
    if order_overrides is not None:
        override_bytes = order_overrides.read_bytes()
        try:
            overrides = json.loads(override_bytes)
        except json.JSONDecodeError as exc:
            raise KnowledgeFoundationError("Cannot read reading-order override JSON.") from exc
        extracted = apply_reading_order(extracted, overrides)
        continuations = overrides.get("continuations", {})
        parser["reading_order_sha256"] = digest_bytes(override_bytes)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run = workspace / source_hash[:16] / stamp
    run.mkdir(parents=True, exist_ok=False)
    snapshot_path = run / "transcription.json"
    prior_content: dict[str, str] = {}
    snapshot_units = []
    for unit in extracted:
        previous = continuations.get(unit.locator) or (prior_content.get(unit.kind) if unit.continues_prev else None)
        snapshot_units.append({"locator": unit.locator, "kind": unit.kind, "text": unit.text, "bbox": unit.bbox, "visual_ref": unit.visual_ref, "raw_content": unit.raw_content, "continues_prev": unit.continues_prev, "continues_from": previous, "continuation_method": "manual" if unit.locator in continuations else "parser" if previous else None})
        if unit.kind not in ("page_number", "header", "page_empty"):
            prior_content[unit.kind] = unit.locator
    snapshot = {
        "schema_version": "knowledge-foundation-transcription/v1",
        "source_sha256": source_hash,
        "parser": parser,
        "units": snapshot_units,
    }
    _write_json(snapshot_path, snapshot)
    units: list[dict[str, Any]] = []
    for order, unit in enumerate(extracted):
        is_structure = unit.kind in ("blank", "page_empty", "page_number", "header", "paragraph_title", "doc_title")
        units.append({
            "id": f"u.{digest_text(unit.locator)[:16]}" if adapter.name == MineruPdfAdapter.name else f"u{order + 1}",
            "order": order, "kind": unit.kind, "locator": unit.locator,
            "content_sha256": digest_text(unit.text) if unit.text else None,
            "transcription_status": "visual_only" if unit.visual_ref or unit.kind == "page_empty" else "machine_extracted" if adapter.name == MineruPdfAdapter.name else "verified",
            "interpretation_status": "not_applicable" if is_structure else "unreviewed",
            "review_note": "Empty PDF page; verify against original." if unit.kind == "page_empty" else "Structure retained; no semantic claim asserted." if is_structure else "Visual body and caption need page review; raw parser content is retained in the snapshot." if unit.visual_ref else "Source content needs human semantic review; OCR text also needs visual verification." if adapter.name == MineruPdfAdapter.name else "Source content needs human semantic review.",
        })
    scope_key = hashlib.sha256((source_hash + (pages or "all")).encode()).hexdigest()[:16]
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": f"artifact-{scope_key}",
        "revision": {"id": "r1-extracted", "parent_id": None, "note": "Source inventory only; semantic annotations have not been reviewed."},
        "source": {
            "id": f"source-{source_hash[:16]}", "kind": "pdf" if source.suffix.lower() == ".pdf" else "text",
            "title": source.stem, "path": str(source_rel), "sha256": source_hash,
            "snapshot_path": str(snapshot_path.relative_to(root)), "snapshot_sha256": digest_bytes(snapshot_path.read_bytes()),
            "transcription_method": f"{parser['name']} {parser.get('version', 'unknown')}; local",
        },
        "scope": {
            "description": f"{source.name}: {pages or 'complete text / selected PDF pages'}",
            "included_locators": [unit.locator for unit in extracted],
            "context_locators": [], "outside_scope_note": "Other source pages or files are outside this extraction run.",
        },
        "units": units, "annotations": [], "relations": [],
    }
    artifact_path = run / "foundation.json"
    _write_json(artifact_path, artifact)
    validate(artifact, root=root, resolve=True)
    return artifact_path, snapshot_path
