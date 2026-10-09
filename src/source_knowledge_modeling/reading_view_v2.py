"""Offline package reading view over one immutable semantic v2 revision."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import html
import json
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError
from . import semantic_v2 as v2


_TEMPLATE = Path(__file__).resolve().parent / "templates" / "semantic-reading-view.html"


def render_reading_view_v2(projection: dict[str, Any], artifact_bytes: bytes, spec: dict[str, Any]) -> str:
    v2.validate_projection(projection)
    allowed = {"schema_version", "artifact_id", "revision_id", "artifact_sha256", "title", "scope_label", "groups", "package_labels"}
    if not isinstance(spec, dict) or set(spec) - allowed or spec.get("schema_version") != v2.VIEW_VERSION:
        raise KnowledgeFoundationError("V2 reading view needs semantic-reading-view/v2 configuration.")
    if spec.get("artifact_id") != projection["artifact_id"] or spec.get("revision_id") != projection["revision"]["id"] or spec.get("artifact_sha256") != sha256(artifact_bytes).hexdigest():
        raise KnowledgeFoundationError("V2 reading view artifact identity, revision or SHA-256 mismatch.")
    if not isinstance(spec.get("title"), str) or not spec["title"].strip() or not isinstance(spec.get("groups"), list) or not spec["groups"]:
        raise KnowledgeFoundationError("V2 reading view needs title and ordered groups.")
    by_package = {p["id"]: p for p in projection["packages"]}
    chosen: list[str] = []
    for group in spec["groups"]:
        if not isinstance(group, dict) or set(group) != {"label", "package_ids"} or not isinstance(group["label"], str) or not group["label"].strip() or not isinstance(group["package_ids"], list) or not group["package_ids"]:
            raise KnowledgeFoundationError("V2 reading view groups need label and package IDs.")
        chosen.extend(group["package_ids"])
    if len(chosen) != len(set(chosen)) or not set(chosen) <= set(by_package):
        raise KnowledgeFoundationError("V2 reading view has duplicate or unknown packages.")
    labels = spec.get("package_labels", {})
    if not isinstance(labels, dict) or not set(labels) <= set(chosen) or any(not isinstance(x, str) or not x.strip() for x in labels.values()):
        raise KnowledgeFoundationError("V2 reading view package labels are invalid.")
    reviews = {r["id"]: r for r in projection["reviews"]}
    statements = []
    visible_locators: set[str] = set()
    for package_id in chosen:
        package = by_package[package_id]
        found = v2.query(projection, package_id=package_id)
        if not found["elements"]:
            raise KnowledgeFoundationError("V2 reading view package has no elements.")
        elements = deepcopy(found["elements"])
        relations = deepcopy(found["relations"])
        for item in [*elements, *relations]:
            visible_locators.update(a["locator"] for a in [*item["anchors"], *item.get("regions", [])])
            if item["review_state"] == "accepted":
                review = reviews[item["review_id"]]
                item["review_label"] = f"{review['reviewer']} / {review['check_method']}（v2 提案）"
            else:
                item["review_label"] = "v1 提案历史核对；v2 待复审"
        statements.append({"id": package_id, "label": labels.get(package_id, package["summary"]), "summary": package["summary"],
                           "origin_type": package["origin_type"], "modality": package["modality"], "elements": elements, "relations": relations,
                           "primary_element_ids": package["element_ids"],
                           "review": {"reviewer": "按要素查看", "check_method": "mixed"}, "scientific_status": "not_assessed", "applicability_status": "not_assessed"})
    gaps = [deepcopy(item) for item in projection["information"] if item["status"] in ("gap", "uncertain", "not_stated") and item["locator"] in visible_locators]
    payload = {"schema_version": v2.VIEW_VERSION, "artifact_schema_version": v2.VERSION, "artifact_id": projection["artifact_id"], "artifact_sha256": spec["artifact_sha256"],
               "source_sha256": projection["source"]["sha256"], "revision": projection["revision"]["id"], "profile": deepcopy(projection["profile"]),
               "title": spec["title"], "scope_label": spec.get("scope_label", projection["source"]["title"]),
               "checks_label": "来源核验和提案审阅按对象显示；旧决定保留原粒度", "groups": [{"label": g["label"], "ids": g["package_ids"]} for g in spec["groups"]],
               "statements": statements, "gaps": gaps}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = _TEMPLATE.read_text(encoding="utf-8")
    if template.count("__PAYLOAD__") != 1:
        raise KnowledgeFoundationError("Reading view template has no unique data slot.")
    return template.replace("__PAYLOAD__", data).replace("<title>语义学习视图</title>", "<title>" + html.escape(spec["title"]) + " · 语义关系图</title>")
