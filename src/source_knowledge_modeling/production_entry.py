"""A local, identity-bound entry point that composes ingestion and preparation."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from .core import KnowledgeFoundationError, load_json, validate
from .ingest import SourceAdapter, ingest
from . import semantic_workflow as workflow


def _file_identity(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    return {"path": str(path.resolve()), "sha256": workflow.file_hash(path)}


def start(source: Path, *, root: Path, output_dir: Path, scope_path: Path,
          profile: dict[str, str], adapter: SourceAdapter,
          adapter_options: dict[str, Any], pages: str | None = None,
          middle_json: Path | None = None, order_overrides: Path | None = None,
          existing_path: Path | None = None, executor: str = "unknown",
          model: str = "unknown") -> dict[str, Any]:
    """Reuse exact local inputs, otherwise require a new entry directory.

    The entry index is saved after ingest so a failed prepare can be retried
    without extracting again. Semantic templates and judgments belong solely
    to prepare_workspace and its existing author/reviewer workflow.
    """
    started = perf_counter()
    source, root, output_dir = source.resolve(), root.resolve(), output_dir.resolve()
    if not source.is_relative_to(root) or not output_dir.is_relative_to(root):
        raise KnowledgeFoundationError("Source and start directory must be inside the caller root.")
    if profile["version"] != "3.0.0":
        raise KnowledgeFoundationError("Start requires instructional-source@3.0.0.")
    scope = load_json(scope_path)
    identity = {"root": str(root), "source": _file_identity(source),
                "scope": _file_identity(scope_path), "profile": profile,
                "adapter": adapter.name, "adapter_options": adapter_options,
                "pages": pages, "middle_json": _file_identity(middle_json),
                "order_overrides": _file_identity(order_overrides),
                "existing": _file_identity(existing_path)}
    entry_path = output_dir / "source-entry.json"
    reused = entry_path.exists()
    if reused:
        entry = load_json(entry_path)
        if entry.get("schema_version") != "knowledge-production-entry/v1" or entry.get("identity") != identity:
            raise KnowledgeFoundationError("Start inputs changed; use a new output directory.")
        paths = {}
        for name in ("foundation", "snapshot"):
            ref = entry.get(name)
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
                raise KnowledgeFoundationError(f"Start frozen {name} identity is invalid; use a new output directory.")
            path = (output_dir / ref["path"]).resolve()
            if not path.is_relative_to(output_dir) or not path.is_file() or workflow.file_hash(path) != ref["sha256"]:
                raise KnowledgeFoundationError(f"Start frozen {name} changed; use a new output directory.")
            paths[name] = path
        foundation_path, snapshot_path = paths["foundation"], paths["snapshot"]
        foundation = load_json(foundation_path)
        validate(foundation, root=root, resolve=True)
        frozen_source = foundation["source"]
        if ((root / frozen_source["path"]).resolve() != source
                or frozen_source["sha256"] != identity["source"]["sha256"]):
            raise KnowledgeFoundationError("Start source identity differs from the frozen foundation; use a new output directory.")
        if (snapshot_path != (root / frozen_source["snapshot_path"]).resolve()
                or entry["snapshot"]["sha256"] != frozen_source["snapshot_sha256"]):
            raise KnowledgeFoundationError("Start snapshot identity differs from the frozen foundation; use a new output directory.")
    else:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise KnowledgeFoundationError("Start needs an empty directory or its own source-entry.json; use a new output directory.")
        foundation_path, snapshot_path = ingest(source, root=root, workspace=output_dir / "source-inventory",
                                               adapter=adapter, pages=pages, order_overrides=order_overrides)
        # A concurrently changed original must never be attributed to this run.
        for name, path in (("source", source), ("scope", scope_path), ("middle_json", middle_json),
                           ("order_overrides", order_overrides), ("existing", existing_path)):
            if _file_identity(path) != identity[name]:
                raise KnowledgeFoundationError(f"Start {name} changed during ingest; use a new output directory.")
        entry = {"schema_version": "knowledge-production-entry/v1", "identity": identity,
                 **{name: {"path": str(path.relative_to(output_dir)), "sha256": workflow.file_hash(path)}
                    for name, path in (("foundation", foundation_path), ("snapshot", snapshot_path))}}
        workflow.write_immutable(entry_path, entry)
    result = workflow.prepare_workspace(foundation_path, root=root, profile=profile,
                                        scope_path=scope_path, output_dir=output_dir,
                                        existing_path=existing_path, executor=executor, model=model)
    return {**result, "artifact": str(foundation_path), "snapshot": str(snapshot_path),
            "ingest_reused": reused, "runbook": str(output_dir / "RUNBOOK.md"),
            "scope": scope, "dependency_policy": "full_scope",
            "elapsed_seconds": round(perf_counter() - started, 6),
            "model": model.strip() or "unknown", "tokens": "unknown"}
