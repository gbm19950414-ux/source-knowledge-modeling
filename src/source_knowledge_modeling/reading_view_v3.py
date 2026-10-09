"""Offline v3 reading view bound to one immutable published revision."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import html
import json
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError
from . import semantic_v3 as v3

VIEW_VERSION = "semantic-reading-view/v3"
_TEMPLATE = Path(__file__).resolve().parent / "templates" / "semantic-reading-view.html"


def default_spec(projection: dict[str, Any], artifact_bytes: bytes) -> dict[str, Any]:
    return {"schema_version": VIEW_VERSION, "artifact_id": projection["artifact_id"],
            "revision_id": projection["revision"]["id"], "artifact_sha256": sha256(artifact_bytes).hexdigest(),
            "title": projection["source"]["title"],
            "groups": [{"label": "来源主题", "package_ids": [p["id"] for p in projection["packages"]]}]}


def render_reading_view_v3(projection: dict[str, Any], artifact_bytes: bytes,
                           spec: dict[str, Any] | None = None) -> str:
    v3.validate_projection(projection)
    if not projection["packages"]:
        raise KnowledgeFoundationError("V3 reading view needs at least one published topic.")
    spec = default_spec(projection, artifact_bytes) if spec is None else spec
    allowed = {"schema_version", "artifact_id", "revision_id", "artifact_sha256", "title", "scope_label", "groups", "package_labels"}
    if not isinstance(spec, dict) or set(spec) - allowed or spec.get("schema_version") != VIEW_VERSION:
        raise KnowledgeFoundationError("V3 reading view needs semantic-reading-view/v3 configuration.")
    if (spec.get("artifact_id") != projection["artifact_id"] or spec.get("revision_id") != projection["revision"]["id"]
            or spec.get("artifact_sha256") != sha256(artifact_bytes).hexdigest()):
        raise KnowledgeFoundationError("V3 view must name the exact published revision and bytes.")
    if not isinstance(spec.get("title"), str) or not spec["title"].strip() or not isinstance(spec.get("groups"), list) or not spec["groups"]:
        raise KnowledgeFoundationError("V3 reading view needs a title and groups.")
    packages = {x["id"]: x for x in projection["packages"]}
    chosen: list[str] = []
    for group in spec["groups"]:
        if set(group) != {"label", "package_ids"} or not group["label"] or not group["package_ids"]:
            raise KnowledgeFoundationError("V3 view groups need labels and package IDs.")
        chosen.extend(group["package_ids"])
    if len(chosen) != len(set(chosen)) or not set(chosen) <= set(packages):
        raise KnowledgeFoundationError("V3 view selects duplicate or absent packages.")
    labels = spec.get("package_labels", {})
    if not isinstance(labels, dict) or not set(labels) <= set(chosen):
        raise KnowledgeFoundationError("V3 package labels are invalid.")
    reviews = {x["id"]: x for x in projection["reviews"]}
    statements = []
    visible_locators: set[str] = set()
    for package_id in chosen:
        package = packages[package_id]
        result = v3.query(projection, package_id=package_id, layer="all")
        elements, relations = deepcopy(result["elements"]), deepcopy(result["relations"])
        if not elements:
            raise KnowledgeFoundationError("V3 topic has no elements.")
        for item in [*elements, *relations]:
            visible_locators.update(a["locator"] for a in item["anchors"])
            item["review_label"] = (f"{reviews[item['review_id']]['reviewer']} / {reviews[item['review_id']]['check_method']}（v3）"
                                    if item["review_state"] == "accepted" else "历史记录／待独立复审")
        ordered_ids = [x["id"] for x in elements]
        statements.append({"id": package_id, "label": labels.get(package_id, package["summary"]),
                           "summary": package["summary"], "origin_type": package["origin_type"],
                           "modality": package["modality"], "elements": elements, "relations": relations,
                           "primary_element_ids": package["element_ids"], "source_order_ids": ordered_ids,
                           "occurrences": result["occurrences"],
                           "review": {"reviewer": "按要素查看", "check_method": "mixed"},
                           "scientific_status": "not_assessed", "applicability_status": "not_assessed"})
    gaps = [deepcopy(item) for item in projection["information"] + projection["discourse"]
            if item["status"] != "modeled" and item["locator"] in visible_locators]
    payload = {"schema_version": VIEW_VERSION, "artifact_schema_version": v3.VERSION,
               "artifact_id": projection["artifact_id"], "artifact_sha256": spec["artifact_sha256"],
               "source_sha256": projection["source"]["sha256"], "revision": projection["revision"]["id"],
               "profile": deepcopy(projection["profile"]), "title": spec["title"],
               "source_units": deepcopy(projection["source_units"]),
               "scope_label": spec.get("scope_label", projection["source"]["title"]),
               "checks_label": "来源核验与语义审阅逐对象记录；项目解释保留其身份与不确定性",
               "groups": [{"label": x["label"], "ids": x["package_ids"]} for x in spec["groups"]],
               "statements": statements, "gaps": gaps}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = _TEMPLATE.read_text(encoding="utf-8")
    if template.count("__PAYLOAD__") != 1:
        raise KnowledgeFoundationError("V3 reading template has no unique payload slot.")
    return template.replace("__PAYLOAD__", data).replace("<title>语义学习视图</title>",
                                                        "<title>" + html.escape(spec["title"]) + " · 语义关系图</title>")
