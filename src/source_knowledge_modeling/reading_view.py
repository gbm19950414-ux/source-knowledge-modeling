"""Offline human reading view over one immutable semantic projection revision."""

from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError, digest_bytes
from .semantic import validate_projection


VIEW_VERSION = "semantic-reading-view/v1"
_TEMPLATE = Path(__file__).resolve().parent / "templates" / "semantic-reading-view.html"


def render_reading_view(projection: dict[str, Any], artifact_bytes: bytes, spec: dict[str, Any]) -> str:
    """Render selected published statements without changing their semantic content."""
    validate_projection(projection)
    if not isinstance(spec, dict) or spec.get("schema_version") != VIEW_VERSION:
        raise KnowledgeFoundationError("Reading view needs semantic-reading-view/v1 configuration.")
    allowed = {"schema_version", "artifact_id", "revision_id", "artifact_sha256", "title", "scope_label", "groups", "statement_labels"}
    required_text = {"schema_version", "artifact_id", "revision_id", "artifact_sha256", "title"}
    if set(spec) - allowed or any(not isinstance(spec.get(key), str) or not spec[key].strip()
                                  for key in required_text):
        raise KnowledgeFoundationError("Reading view configuration has missing or unsupported fields.")
    if "scope_label" in spec and (not isinstance(spec["scope_label"], str) or not spec["scope_label"].strip()):
        raise KnowledgeFoundationError("Reading view scope_label must be nonempty text.")
    if (spec["artifact_id"] != projection["artifact_id"]
            or spec["revision_id"] != projection["revision"]["id"]
            or spec["artifact_sha256"] != digest_bytes(artifact_bytes)):
        raise KnowledgeFoundationError("Reading view artifact identity, revision or SHA-256 mismatch.")
    groups = spec.get("groups")
    if not isinstance(groups, list) or not groups:
        raise KnowledgeFoundationError("Reading view needs nonempty ordered groups.")
    by_id = {statement["id"]: statement for statement in projection["statements"]}
    chosen: list[str] = []
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {"label", "statement_ids"}
                or not isinstance(group["label"], str) or not group["label"].strip()
                or not isinstance(group["statement_ids"], list) or not group["statement_ids"]
                or any(not isinstance(i, str) for i in group["statement_ids"])):
            raise KnowledgeFoundationError("Reading view groups need a label and nonempty statement_ids.")
        chosen.extend(group["statement_ids"])
    if len(set(chosen)) != len(chosen) or any(i not in by_id for i in chosen):
        raise KnowledgeFoundationError("Reading view contains duplicate or unknown statement IDs.")
    labels = spec.get("statement_labels", {})
    if (not isinstance(labels, dict) or not set(labels) <= set(chosen)
            or any(not isinstance(value, str) or not value.strip() for value in labels.values())):
        raise KnowledgeFoundationError("Reading view statement_labels must name selected statements.")
    statements = []
    for statement_id in chosen:
        statement = deepcopy(by_id[statement_id])
        statement["label"] = labels.get(statement_id, statement["summary"])
        statements.append(statement)
    reviewers = sorted({s["review"]["reviewer"] for s in statements})
    payload = {
        "schema_version": VIEW_VERSION,
        "artifact_id": projection["artifact_id"],
        "artifact_sha256": spec["artifact_sha256"],
        "source_sha256": projection["source"]["sha256"],
        "revision": projection["revision"]["id"],
        "profile": deepcopy(projection["profile"]),
        "title": spec["title"],
        "scope_label": spec.get("scope_label", projection["source"]["title"]),
        "checks_label": "来源审阅记录：" + "、".join(reviewers) + " · 科学与个人适用性状态见要素详情",
        "groups": [{"label": group["label"], "ids": group["statement_ids"]} for group in groups],
        "statements": statements,
    }
    # Escape '<' so even a malicious or unusual source quote cannot close the data script.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = _TEMPLATE.read_text(encoding="utf-8")
    if template.count("__PAYLOAD__") != 1:
        raise KnowledgeFoundationError("Reading view template has no unique data slot.")
    return template.replace("__PAYLOAD__", data).replace("<title>语义学习视图</title>",
                                                       "<title>" + html.escape(spec["title"]) + " · 语义关系图</title>")
