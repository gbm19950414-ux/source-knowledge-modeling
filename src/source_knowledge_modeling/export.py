"""Versioned, read-only handoff for downstream knowledge consumers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError, validate


EXPORT_VERSION = "source-knowledge-export/v1"


def export_manifest(artifact: dict[str, Any], *, root: Path, producer_commit: str) -> dict[str, Any]:
    validate(artifact, root=root, resolve=True)
    if not producer_commit.strip():
        raise KnowledgeFoundationError("A producing commit or immutable release identifier is required.")
    try:
        package_version = version("source-knowledge-modeling")
    except PackageNotFoundError:
        package_version = "0.2.0+source"
    units = {unit["id"]: unit for unit in artifact["units"]}
    return {
        "schema_version": EXPORT_VERSION,
        "producer": {"name": "source-knowledge-modeling", "version": package_version, "commit_or_release": producer_commit.strip()},
        "artifact_id": artifact["artifact_id"],
        "artifact_schema_version": artifact["schema_version"],
        "artifact_revision_id": artifact["revision"]["id"],
        "source": {
            "id": artifact["source"]["id"], "kind": artifact["source"]["kind"],
            "sha256": artifact["source"]["sha256"], "snapshot_sha256": artifact["source"]["snapshot_sha256"],
        },
        "units": [{"id": unit["id"], "locator": unit["locator"], "kind": unit["kind"], "transcription_status": unit["transcription_status"], "interpretation_status": unit["interpretation_status"]} for unit in artifact["units"]],
        "annotations": [
            {**annotation, "source_anchors": [units[unit_id]["locator"] for unit_id in annotation["unit_ids"]]}
            for annotation in artifact["annotations"]
        ],
        "relations": [
            {**relation, "source_anchors": [units[unit_id]["locator"] for unit_id in relation["support_unit_ids"]]}
            for relation in artifact["relations"]
        ],
    }
