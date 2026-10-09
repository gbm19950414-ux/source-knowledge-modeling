"""Independent object graph and proposal-level review for semantic projection v2."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .core import KnowledgeFoundationError, load_json
from .semantic import anchor_key, profile_manifest, validate_projection as validate_v1


VERSION = "knowledge-semantic-projection/v2"
DRAFT_VERSION = "knowledge-semantic-drafts/v2"
REVIEW_VERSION = "knowledge-semantic-review/v2"
SOURCE_CHECK_VERSION = "knowledge-source-check/v1"
WORK_PACKAGE_VERSION = "knowledge-semantic-work-package/v1"
EXPORT_VERSION = "source-knowledge-export/v3"
VIEW_VERSION = "semantic-reading-view/v2"
_CONTENT_KEYS = {
    "element": ("kind", "text", "origin_type", "modality", "negated", "example_scope", "quantity", "anchors", "regions", "context_ids"),
    "relation": ("type", "from_id", "to_id", "from_revision", "to_revision", "note", "anchors", "regions", "context_ids"),
}
_RELATION_SOURCE_KINDS = {"conditions": "condition", "exception_to": "exception", "replaces": "action", "supports": "reason", "precedes": "action", "quantifies": "parameter", "example_of": "example"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def content_revision(value: dict[str, Any], kind: str) -> str:
    return digest({key: value[key] for key in _CONTENT_KEYS[kind] if key in value})


def seal(value: dict[str, Any], kind: str) -> dict[str, Any]:
    result = deepcopy(value)
    result["content_revision"] = content_revision(result, kind)
    return result


def _unique(items: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise KnowledgeFoundationError(f"Duplicate {name} ID.")
    return {item["id"]: item for item in items}


def _schema() -> dict[str, Any]:
    return load_json(Path(__file__).resolve().parent / "schemas/knowledge-semantic-projection-v2.schema.json")


def _source_texts(projection: dict[str, Any], root: Path) -> dict[str, str]:
    source = projection["source"]
    for key, hash_key in (("path", "sha256"), ("snapshot_path", "snapshot_sha256")):
        path = root / source[key]
        try:
            actual = sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise KnowledgeFoundationError(f"Source file unavailable: {path}") from exc
        if actual != source[hash_key]:
            raise KnowledgeFoundationError(f"Source revision mismatch: {path}")
    snapshot = load_json(root / source["snapshot_path"])
    return {unit["locator"]: unit["text"] for unit in snapshot["units"]}


def _check_anchor(anchor: dict[str, Any], locators: set[str], texts: dict[str, str] | None) -> None:
    if anchor["locator"] not in locators or anchor["end"] <= anchor["start"]:
        raise KnowledgeFoundationError(f"Invalid v2 anchor: {anchor}")
    if texts is not None and texts.get(anchor["locator"], "")[anchor["start"]:anchor["end"]] != anchor["quote"]:
        raise KnowledgeFoundationError(f"V2 anchor quote mismatch: {anchor['locator']}")


def region_key(region: dict[str, Any]) -> str:
    return digest(region)


def _check_region(region: dict[str, Any], locators: set[str], units: dict[str, dict[str, Any]] | None) -> None:
    if region["locator"] not in locators or len(region["bbox"]) != 4 or not all(0 <= x <= 1 for x in region["bbox"]):
        raise KnowledgeFoundationError(f"V2 visual region outside scope: {region['locator']}")
    if units is not None:
        unit = units.get(region["locator"])
        if unit is None or unit["kind"] != region["native_kind"] or unit["bbox"] != region["bbox"] or digest(unit["raw_content"]) != region["native_structure_sha256"]:
            raise KnowledgeFoundationError(f"V2 visual region/native structure mismatch: {region['locator']}")


def validate_projection(projection: dict[str, Any], *, root: Path | None = None, resolve: bool = False) -> dict[str, Any]:
    errors = list(Draft202012Validator(_schema()).iter_errors(projection))
    if errors:
        error = errors[0]
        raise KnowledgeFoundationError(f"V2 schema error at {list(error.path)}: {error.message}")
    if projection["profile"] != profile_manifest(projection["profile"]["id"], projection["profile"]["version"]):
        raise KnowledgeFoundationError("V2 profile definition hash mismatch.")
    if resolve and root is None:
        raise KnowledgeFoundationError("Resolved v2 validation needs source root.")
    texts = _source_texts(projection, root) if resolve and root is not None else None
    units = {u["locator"]: u for u in load_json(root / projection["source"]["snapshot_path"])["units"]} if resolve and root is not None else None
    locators = projection["scope"]["included_locators"]
    if len(locators) != len(set(locators)) or len(projection["screening"]) != len(locators):
        raise KnowledgeFoundationError("V2 scope/screening must cover each source locator once.")
    if {x["locator"] for x in projection["screening"]} != set(locators):
        raise KnowledgeFoundationError("V2 screening does not match scope.")
    elements = _unique(projection["elements"], "element")
    relations = _unique(projection["relations"], "relation")
    packages = _unique(projection["packages"], "package")
    checks = _unique(projection["source_checks"], "source check")
    reviews = _unique(projection["reviews"], "review")
    information = _unique(projection["information"], "information")
    if set(elements) & set(relations):
        raise KnowledgeFoundationError("Element and relation IDs share a namespace.")
    context_pool = set(elements) | set(relations)
    for item in [*elements.values(), *relations.values(), *packages.values()]:
        if not set(item["context_ids"]) <= context_pool or item["id"] in item["context_ids"]:
            raise KnowledgeFoundationError(f"V2 context dependency is absent or self-referential: {item['id']}")
    for item in [*elements.values(), *relations.values()]:
        kind = "element" if item["id"] in elements else "relation"
        if item["content_revision"] != content_revision(item, kind):
            raise KnowledgeFoundationError(f"V2 content revision mismatch: {item['id']}")
        for anchor in item["anchors"]:
            _check_anchor(anchor, set(locators), texts)
        for region in item.get("regions", []):
            _check_region(region, set(locators), units)
        if item["review_state"] == "accepted":
            review = reviews.get(item["review_id"])
            if item["legacy_review_package_id"] is not None or not review or not item["source_check_ids"]:
                raise KnowledgeFoundationError(f"V2 accepted object lacks distinct check/review: {item['id']}")
            if projection["source"]["kind"] == "pdf" and review["check_method"] != "original_visual":
                raise KnowledgeFoundationError(f"V2 PDF semantic review must cite original page: {item['id']}")
            required = {anchor_key(anchor) for anchor in item["anchors"]}
            required_regions = {region_key(region) for region in item.get("regions", [])}
            if not required <= set(review["checked_anchor_keys"]) or not required_regions <= set(review.get("checked_region_keys", [])):
                raise KnowledgeFoundationError(f"V2 review does not cover object: {item['id']}")
            checked: set[str] = set(); checked_regions: set[str] = set()
            for check_id in item["source_check_ids"]:
                check = checks.get(check_id)
                if not check or check["source_sha256"] != projection["source"]["sha256"] or check["snapshot_sha256"] != projection["source"]["snapshot_sha256"]:
                    raise KnowledgeFoundationError(f"V2 source check identity mismatch: {item['id']}")
                if projection["source"]["kind"] == "pdf" and check["check_method"] != "original_visual":
                    raise KnowledgeFoundationError(f"PDF check must use original page: {item['id']}")
                checked.update(check["anchor_keys"])
                checked_regions.update(check.get("region_keys", []))
            if not required <= checked or not required_regions <= checked_regions:
                raise KnowledgeFoundationError(f"V2 source check does not cover object: {item['id']}")
        elif item["review_id"] is not None or item["source_check_ids"] or item["legacy_review_package_id"] not in packages:
            raise KnowledgeFoundationError(f"V2 historical object has invented review provenance: {item['id']}")
    for element in elements.values():
        if (element["kind"] == "parameter") != ("quantity" in element):
            raise KnowledgeFoundationError(f"V2 quantity assignment mismatch: {element['id']}")
        if element["kind"] == "parameter" and not any(r["type"] == "quantifies" and r["from_id"] == element["id"] for r in relations.values()):
            raise KnowledgeFoundationError(f"V2 quantity lacks target: {element['id']}")
    for relation in relations.values():
        source, target = elements.get(relation["from_id"]), elements.get(relation["to_id"])
        if not source or not target or source is target or relation["from_revision"] != source["content_revision"] or relation["to_revision"] != target["content_revision"]:
            raise KnowledgeFoundationError(f"V2 relation endpoint/revision mismatch: {relation['id']}")
        required = _RELATION_SOURCE_KINDS.get(relation["type"])
        if required and source["kind"] != required:
            raise KnowledgeFoundationError(f"V2 relation direction mismatch: {relation['id']}")
        if relation["type"] in ("precedes", "replaces", "conditions", "exception_to") and target["kind"] != "action":
            raise KnowledgeFoundationError(f"V2 relation target mismatch: {relation['id']}")
        if relation["type"] == "supports" and target["kind"] not in ("action", "assertion"):
            raise KnowledgeFoundationError(f"V2 support target mismatch: {relation['id']}")
        if relation["type"] == "quantifies" and target["kind"] not in ("action", "assertion", "example", "exception"):
            raise KnowledgeFoundationError(f"V2 quantity target mismatch: {relation['id']}")
    successors: dict[str, list[str]] = {}
    for relation in relations.values():
        if relation["type"] == "precedes":
            successors.setdefault(relation["from_id"], []).append(relation["to_id"])
    active: set[str] = set(); visited: set[str] = set()
    def walk(node: str) -> None:
        if node in active:
            raise KnowledgeFoundationError("V2 cyclic step order.")
        if node in visited:
            return
        active.add(node)
        for successor in successors.get(node, []):
            walk(successor)
        active.remove(node); visited.add(node)
    for node in successors:
        walk(node)
    referenced_elements: set[str] = set(); referenced_relations: set[str] = set()
    for package in packages.values():
        if not set(package["element_ids"]) <= set(elements) or not set(package["relation_ids"]) <= set(relations):
            raise KnowledgeFoundationError(f"V2 package references missing objects: {package['id']}")
        referenced_elements.update(package["element_ids"]); referenced_relations.update(package["relation_ids"])
        if package["legacy_review"] is not None and not isinstance(package["legacy_review"], dict):
            raise KnowledgeFoundationError(f"V2 invalid historical review: {package['id']}")
    if referenced_elements != set(elements) or referenced_relations != set(relations):
        raise KnowledgeFoundationError("V2 objects must belong to at least one package.")
    for item in information.values():
        if item["locator"] not in locators or item["end"] <= item["start"]:
            raise KnowledgeFoundationError(f"V2 information outside scope: {item['id']}")
        if item["status"] == "modeled" and not item["model_refs"]:
            raise KnowledgeFoundationError(f"V2 modeled information has no object: {item['id']}")
        if item["status"] == "not_stated" and (not item.get("checked_scope") or not set(item["checked_scope"]) <= set(locators)):
            raise KnowledgeFoundationError(f"V2 not-stated information lacks an explicit checked scope: {item['id']}")
        if not set(item["model_refs"]) <= set(elements) | set(relations):
            raise KnowledgeFoundationError(f"V2 information has missing references: {item['id']}")
    for link in projection["lineage"]:
        pool = {"package": packages, "element": elements, "relation": relations}[link["new_kind"]]
        if not set(link["new_ids"]) <= set(pool):
            raise KnowledgeFoundationError(f"V2 lineage points to absent object: {link['old_id']}")
    return {"revision_id": projection["revision"]["id"], "element_count": len(elements), "relation_count": len(relations), "package_count": len(packages), "information_status": dict(Counter(x["status"] for x in information.values()))}


def migrate_v1(old: dict[str, Any], old_bytes: bytes, *, root: Path, revision_id: str) -> dict[str, Any]:
    validate_v1(old, root=root, resolve=True)
    if old["schema_version"] != "knowledge-semantic-projection/v1":
        raise KnowledgeFoundationError("V2 migration needs a v1 semantic projection.")
    profile = profile_manifest("instructional-source", "2.0.0")
    result = {"schema_version": VERSION, "artifact_id": old["artifact_id"], "revision": {"id": revision_id, "parent_id": old["revision"]["id"], "note": "Explicit v1 object migration; historical proposal reviews retained without new acceptance."},
              "profile": profile, "source": deepcopy(old["source"]), "legacy": {"schema_version": old["schema_version"], "artifact_id": old["artifact_id"], "revision_id": old["revision"]["id"], "sha256": sha256(old_bytes).hexdigest()},
              "scope": deepcopy(old["scope"]), "screening": deepcopy(old["screening"]), "information": [], "elements": [], "relations": [], "packages": [], "source_checks": [], "reviews": [], "lineage": []}
    used_elements: set[str] = set(); used_relations: set[str] = set()
    for statement in old["statements"]:
        element_ids: dict[str, str] = {}
        package = {"id": statement["id"], "summary": statement["summary"], "origin_type": statement["origin_type"], "modality": statement["modality"], "element_ids": [], "relation_ids": [], "context_ids": [], "legacy_review": deepcopy(statement["review"])}
        for source in statement["elements"]:
            object_id = source["id"] if source["id"] not in used_elements else f"{statement['id']}--{source['id']}"
            used_elements.add(object_id); element_ids[source["id"]] = object_id
            obj = {"id": object_id, "kind": source["kind"], "text": source["text"], "origin_type": statement["origin_type"], "modality": statement["modality"], "negated": statement["modality"] == "negated", "example_scope": statement["id"] if statement["origin_type"] == "source_example" else None,
                   "anchors": deepcopy(source["anchors"]), "context_ids": [], "source_check_ids": [], "review_state": "legacy_historical", "review_id": None, "legacy_review_package_id": statement["id"]}
            if "quantity" in source:
                obj["quantity"] = {**deepcopy(source["quantity"]), "scope": statement["id"]}
            obj = seal(obj, "element"); result["elements"].append(obj); package["element_ids"].append(object_id)
            result["lineage"].append({"old_kind": "element", "old_id": f"{statement['id']}::{source['id']}", "new_kind": "element", "new_ids": [object_id], "change": "migrated", "reason": "Explicitly scoped v1 ID; historical proposal review only."})
        current_elements = {e["id"]: e for e in result["elements"]}
        for source in statement["relations"]:
            object_id = source["id"] if source["id"] not in used_relations and source["id"] not in used_elements else f"{statement['id']}--{source['id']}"
            used_relations.add(object_id)
            from_id, to_id = element_ids[source["from_id"]], element_ids[source["to_id"]]
            obj = {"id": object_id, "type": source["type"], "from_id": from_id, "to_id": to_id, "from_revision": current_elements[from_id]["content_revision"], "to_revision": current_elements[to_id]["content_revision"], "anchors": deepcopy(source["anchors"]), "note": source["note"], "context_ids": [], "source_check_ids": [], "review_state": "legacy_historical", "review_id": None, "legacy_review_package_id": statement["id"]}
            obj = seal(obj, "relation"); result["relations"].append(obj); package["relation_ids"].append(object_id)
            result["lineage"].append({"old_kind": "relation", "old_id": f"{statement['id']}::{source['id']}", "new_kind": "relation", "new_ids": [object_id], "change": "migrated", "reason": "Explicitly scoped v1 ID; historical proposal review only."})
        result["packages"].append(package)
        result["lineage"].append({"old_kind": "statement", "old_id": statement["id"], "new_kind": "package", "new_ids": [statement["id"]], "change": "migrated", "reason": "Package preserves the old statement summary and proposal-level review."})
    validate_projection(result, root=root, resolve=True)
    return result


def prepare(foundation: dict[str, Any], foundation_bytes: bytes, scope: dict[str, Any], profile: dict[str, str], *, existing: dict[str, Any] | None = None, existing_bytes: bytes | None = None) -> dict[str, Any]:
    if foundation.get("schema_version") != "knowledge-foundation/v1":
        raise KnowledgeFoundationError("V2 prepare needs a foundation v1 artifact.")
    if profile != profile_manifest(profile["id"], profile["version"]):
        raise KnowledgeFoundationError("V2 prepare profile hash mismatch.")
    included = scope.get("included_locators")
    context = scope.get("context_locators", [])
    units = {unit["locator"]: unit for unit in foundation["units"]}
    if not isinstance(included, list) or not included or len(included) != len(set(included)) or not isinstance(context, list) or not set(included + context) <= set(units):
        raise KnowledgeFoundationError("V2 prepare scope needs unique known locators and valid context.")
    if existing is not None and existing.get("schema_version") not in (VERSION, "knowledge-semantic-projection/v1"):
        raise KnowledgeFoundationError("V2 prepare existing artifact must be a semantic projection.")
    existing_ids = set()
    source_changed = False
    if existing is not None:
        source_changed = existing["source"]["sha256"] != foundation["source"]["sha256"] or existing["source"]["snapshot_sha256"] != foundation["source"]["snapshot_sha256"]
        if source_changed:
            mappings = scope.get("anchor_map")
            old_locators=set(existing["scope"]["included_locators"])
            if not isinstance(mappings, list) or not mappings or any(not isinstance(x, dict) or set(x) != {"old_locator", "new_locator", "reason"} or x["old_locator"] not in old_locators or x["new_locator"] not in units or not x["reason"] for x in mappings):
                raise KnowledgeFoundationError("V2 source revision needs explicit old/new locator mappings; unchanged offsets are never inherited.")
        existing_ids = {p["id"] for p in existing.get("packages", existing.get("statements", []))}
    identity = {"foundation_sha256": sha256(foundation_bytes).hexdigest(), "source_sha256": foundation["source"]["sha256"], "snapshot_sha256": foundation["source"]["snapshot_sha256"], "profile_sha256": profile["definition_sha256"], "scope": scope, "existing_sha256": sha256(existing_bytes).hexdigest() if existing_bytes is not None else None}
    chosen = list(dict.fromkeys([*included, *context]))
    return {"schema_version": WORK_PACKAGE_VERSION, "artifact_id": foundation["artifact_id"], "input_fingerprint": digest(identity), "inputs": identity, "profile": profile,
            "scope": deepcopy(scope), "source_units": [deepcopy(units[i]) for i in chosen], "current_objects": sorted(existing_ids),
            "tasks": [{"locator": locator, "status": "inspect_source_information", "reason": "Compare all meanings with current objects and record concrete gaps."} for locator in included] + ([{"status":"verify_anchor_mapping","old_locator":x["old_locator"],"new_locator":x["new_locator"],"reason":x["reason"]} for x in scope["anchor_map"]] + [{"status":"pending_anchor_alignment","old_locator":locator,"reason":"No reliable old-to-new locator mapping; preserve old position and inspect manually."} for locator in sorted(set(existing["scope"]["included_locators"])-{x["old_locator"] for x in scope["anchor_map"]})] if source_changed else []),
            "steps": {name: "pending" for name in ("source_information", "draft", "source_check", "semantic_review", "publish", "derive")}}


def _proposal_anchors(base: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    elements = {x["id"]: x for x in base["elements"]}; relations = {x["id"]: x for x in base["relations"]}
    changed = proposal["upsert_elements"] + proposal["upsert_relations"]
    retired = [elements[x] for x in proposal["retire_elements"]] + [relations[x] for x in proposal["retire_relations"]]
    return [a for obj in [*changed, *retired] for a in obj["anchors"]]


def _dependency(base: dict[str, Any], proposal: dict[str, Any]) -> str:
    objects = {x["id"]: x["content_revision"] for x in [*base["elements"], *base["relations"]]}
    deps = set()
    for obj in proposal["upsert_elements"] + proposal["upsert_relations"]:
        deps.update(obj["context_ids"])
        if "from_id" in obj:
            deps.update((obj["from_id"], obj["to_id"]))
    return digest({"profile": base["profile"], "source": {k: base["source"][k] for k in ("sha256", "snapshot_sha256")}, "rules": proposal["rule_ids"], "objects": {i: objects.get(i) for i in sorted(deps)}})


def validate_drafts(base: dict[str, Any], drafts: dict[str, Any], *, root: Path) -> None:
    validate_projection(base, root=root, resolve=True)
    if drafts.get("schema_version") != DRAFT_VERSION or drafts.get("artifact_id") != base["artifact_id"] or drafts.get("base_revision_id") != base["revision"]["id"] or drafts.get("base_sha256") != digest(base):
        raise KnowledgeFoundationError("V2 draft base or format mismatch.")
    proposals = drafts.get("proposals")
    if not isinstance(proposals, list) or not proposals or len({p.get("id") for p in proposals}) != len(proposals):
        raise KnowledgeFoundationError("V2 drafts need unique proposals.")
    old_e={x["id"] for x in base["elements"]}; old_r={x["id"] for x in base["relations"]}; old_p={x["id"] for x in base["packages"]}
    rules=set(load_json(Path(__file__).resolve().parent/'profiles'/f"{base['profile']['id']}-{base['profile']['version']}.json")["rules"])
    texts = _source_texts(base, root)
    units = {u["locator"]:u for u in load_json(root / base["source"]["snapshot_path"])["units"]}
    for proposal in proposals:
        required={"id", "reason", "upsert_elements", "upsert_relations", "upsert_packages", "upsert_information", "retire_elements", "retire_relations", "retire_packages", "lineage", "rule_ids"}
        if set(proposal)!=required or not proposal["reason"] or not set(proposal["rule_ids"])<=rules:
            raise KnowledgeFoundationError(f"V2 proposal shape/rules mismatch: {proposal.get('id')}")
        if not set(proposal["retire_elements"])<=old_e or not set(proposal["retire_relations"])<=old_r or not set(proposal["retire_packages"])<=old_p:
            raise KnowledgeFoundationError(f"V2 proposal retires unknown object: {proposal['id']}")
        for link in proposal["lineage"]:
            if not isinstance(link,dict) or not {"old_kind","old_id","new_kind","new_ids","change","reason"}<=set(link) or not link["reason"] or (link["change"]=="retired")!=(not link["new_ids"]):
                raise KnowledgeFoundationError(f"V2 lineage must explain every split, merge, replacement or retirement: {proposal['id']}")
        required_lineage={("element",i) for i in proposal["retire_elements"]}|{("relation",i) for i in proposal["retire_relations"]}|{("statement",i) for i in proposal["retire_packages"]}
        old_elements={x["id"]:x for x in base["elements"]};old_relations={x["id"]:x for x in base["relations"]}
        required_lineage.update(("element",x["id"]) for x in proposal["upsert_elements"] if x["id"] in old_elements and x["content_revision"]!=old_elements[x["id"]]["content_revision"])
        required_lineage.update(("relation",x["id"]) for x in proposal["upsert_relations"] if x["id"] in old_relations and x["content_revision"]!=old_relations[x["id"]]["content_revision"])
        observed_lineage={(x["old_kind"],x["old_id"]) for x in proposal["lineage"]}
        if not required_lineage <= observed_lineage:
            raise KnowledgeFoundationError(f"V2 replacement/retirement lacks old-to-new lineage: {proposal['id']}")
        for kind,key in (("element","upsert_elements"),("relation","upsert_relations")):
            for obj in proposal[key]:
                if obj["content_revision"]!=content_revision(obj,kind) or obj["review_state"]!="accepted" or obj["review_id"] is not None or obj["legacy_review_package_id"] is not None:
                    raise KnowledgeFoundationError(f"V2 draft object is not sealed or carries a decision: {obj['id']}")
                for a in obj["anchors"]:
                    _check_anchor(a,set(base["scope"]["included_locators"]),texts)
                for region in obj.get("regions", []):
                    _check_region(region,set(base["scope"]["included_locators"]),units)


def new_review_ledger(base: dict[str, Any], drafts: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": REVIEW_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"], "draft_sha256": digest(drafts), "decisions": []}


def new_source_check_ledger(base: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SOURCE_CHECK_VERSION, "artifact_id": base["artifact_id"], "source_sha256": base["source"]["sha256"], "snapshot_sha256": base["source"]["snapshot_sha256"], "checks": []}


def record_source_check(base: dict[str, Any], ledger: dict[str, Any], *, check_id: str, anchors: list[dict[str, Any]], reviewer: str, method: str, note: str, root: Path, regions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    validate_projection(base,root=root,resolve=True)
    if ledger.get("schema_version")!=SOURCE_CHECK_VERSION or ledger.get("artifact_id")!=base["artifact_id"] or ledger.get("source_sha256")!=base["source"]["sha256"] or ledger.get("snapshot_sha256")!=base["source"]["snapshot_sha256"]:
        raise KnowledgeFoundationError("V2 source-check ledger identity mismatch.")
    regions = regions or []
    if not check_id or not reviewer.strip() or not (anchors or regions) or method not in ("original_visual","source_text") or base["source"]["kind"]=="pdf" and method!="original_visual":
        raise KnowledgeFoundationError("V2 source check requires a new ID, real method and spans.")
    texts=_source_texts(base,root)
    for anchor in anchors:
        _check_anchor(anchor,set(base["scope"]["included_locators"]),texts)
    units={u["locator"]:u for u in load_json(root / base["source"]["snapshot_path"])["units"]}
    for region in regions:
        _check_region(region,set(base["scope"]["included_locators"]),units)
    keys = sorted({anchor_key(a) for a in anchors})
    region_keys = sorted({region_key(x) for x in regions})
    prior = next((x for x in ledger["checks"] if x["id"] == check_id), None)
    if prior is not None:
        if prior["anchor_keys"] == keys and prior.get("region_keys", []) == region_keys and prior["check_method"] == method and prior["reviewer"] == reviewer.strip() and prior["note"] == note:
            return deepcopy(ledger)
        raise KnowledgeFoundationError("V2 source check ID already names a different event.")
    later=deepcopy(ledger)
    later["checks"].append({"id":check_id,"source_sha256":base["source"]["sha256"],"snapshot_sha256":base["source"]["snapshot_sha256"],"anchor_keys":keys,"region_keys":region_keys,"check_method":method,"reviewer":reviewer.strip(),"checked_at":datetime.now(timezone.utc).isoformat(),"note":note})
    return later


def record_decision(base: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], source_checks: dict[str, Any], *, proposal_id: str, decision: str, reviewer: str, checked_anchors: list[str], check_method: str, note: str, root: Path, checked_regions: list[str] | None = None) -> dict[str, Any]:
    validate_drafts(base,drafts,root=root)
    if ledger.get("schema_version")!=REVIEW_VERSION or ledger.get("draft_sha256")!=digest(drafts) or ledger.get("base_revision_id")!=base["revision"]["id"]:
        raise KnowledgeFoundationError("V2 review ledger identity mismatch.")
    if source_checks.get("schema_version")!=SOURCE_CHECK_VERSION or source_checks.get("artifact_id")!=base["artifact_id"] or source_checks.get("source_sha256")!=base["source"]["sha256"] or source_checks.get("snapshot_sha256")!=base["source"]["snapshot_sha256"]:
        raise KnowledgeFoundationError("V2 source-check ledger mismatch.")
    proposal=next((p for p in drafts["proposals"] if p["id"]==proposal_id),None)
    if proposal is None or decision not in ("accept","reject","pending") or not reviewer.strip():
        raise KnowledgeFoundationError("V2 review needs proposal, decision and reviewer.")
    required={anchor_key(a) for a in _proposal_anchors(base,proposal)}
    checked=set(checked_anchors)
    if not checked<=required or decision=="accept" and not required<=checked:
        raise KnowledgeFoundationError("V2 semantic review lacks exact changed/retired spans.")
    if decision=="accept" and base["source"]["kind"]=="pdf" and check_method!="original_visual":
        raise KnowledgeFoundationError("V2 PDF semantic acceptance needs original visual check.")
    independent={key for item in source_checks["checks"] for key in item["anchor_keys"]}
    new_required={anchor_key(a) for obj in proposal["upsert_elements"]+proposal["upsert_relations"] for a in obj["anchors"]}
    required_regions={region_key(region) for obj in proposal["upsert_elements"]+proposal["upsert_relations"] for region in obj.get("regions", [])}
    checked_regions_set=set(checked_regions or [])
    independent_regions={key for item in source_checks["checks"] for key in item.get("region_keys", [])}
    if not checked_regions_set<=required_regions or decision=="accept" and (not required_regions<=checked_regions_set or not required_regions<=independent_regions):
        raise KnowledgeFoundationError("V2 semantic review lacks exact visual regions or separate visual checks.")
    if decision=="accept" and not new_required<=independent:
        raise KnowledgeFoundationError("V2 accepted proposal lacks separate source checks.")
    details={"proposal_id":proposal_id,"base_revision_id":base["revision"]["id"],"decision":decision,"reviewer":reviewer.strip(),"checked_anchor_keys":sorted(checked),"checked_region_keys":sorted(checked_regions_set),"check_method":check_method,"note":note,"proposal_sha256":digest(proposal),"dependency_sha256":_dependency(base,proposal),"rule_ids":proposal["rule_ids"]}
    prior=next((x for x in ledger["decisions"] if x["proposal_id"]==proposal_id),None)
    if prior is not None:
        if all(prior.get(key)==value for key,value in details.items()):
            return deepcopy(ledger)
        raise KnowledgeFoundationError("V2 proposal already has a different review event; use a new proposal ID.")
    later=deepcopy(ledger)
    later["decisions"].append({**details,"reviewed_at":datetime.now(timezone.utc).isoformat()})
    return later


def publish(base: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], source_checks: dict[str, Any], *, revision_id: str, root: Path) -> dict[str, Any]:
    validate_drafts(base,drafts,root=root)
    if not revision_id or revision_id==base["revision"]["id"] or ledger.get("draft_sha256")!=digest(drafts):
        raise KnowledgeFoundationError("V2 publish identity mismatch.")
    accepted={x["proposal_id"]:x for x in ledger["decisions"] if x["decision"]=="accept"}
    if not accepted:
        raise KnowledgeFoundationError("V2 publish needs accepted proposals.")
    later=deepcopy(base)
    later["revision"]={"id":revision_id,"parent_id":base["revision"]["id"],"note":"Published source-checked v2 semantic changes."}
    reviewed_upserts: set[str] = set()
    for check in source_checks["checks"]:
        if check["id"] not in {x["id"] for x in later["source_checks"]}:
            later["source_checks"].append(deepcopy(check))
    for proposal in drafts["proposals"]:
        decision=accepted.get(proposal["id"])
        if decision is None:
            continue
        if decision["proposal_sha256"]!=digest(proposal) or decision["dependency_sha256"]!=_dependency(base,proposal):
            raise KnowledgeFoundationError(f"V2 proposal changed after review: {proposal['id']}")
        required={anchor_key(a) for a in _proposal_anchors(base,proposal)}
        required_regions={region_key(region) for obj in proposal["upsert_elements"]+proposal["upsert_relations"] for region in obj.get("regions", [])}
        if not required<=set(decision["checked_anchor_keys"]) or not required_regions<=set(decision.get("checked_region_keys", [])):
            raise KnowledgeFoundationError(f"V2 proposal lacks checked spans: {proposal['id']}")
        review_id=f"review-{proposal['id']}-{decision['proposal_sha256'][:12]}"
        if any(x["id"]==review_id for x in later["reviews"]):
            raise KnowledgeFoundationError("V2 duplicate review event.")
        later["reviews"].append({"id":review_id,**deepcopy(decision)})
        for key,retire,upsert in (("elements","retire_elements","upsert_elements"),("relations","retire_relations","upsert_relations"),("packages","retire_packages","upsert_packages"),("information","__none__","upsert_information")):
            old={x["id"]:x for x in later[key]}
            for object_id in proposal.get(retire,[]):
                old.pop(object_id,None)
            for item in proposal[upsert]:
                new=deepcopy(item)
                if key in ("elements","relations"):
                    new["review_id"]=review_id
                    reviewed_upserts.add(new["id"])
                old[new["id"]]=new
            later[key]=list(old.values())
        later["lineage"].extend(deepcopy(proposal["lineage"]))
    old_revisions={x["id"]:x["content_revision"] for x in base["elements"]+base["relations"]}
    new_revisions={x["id"]:x["content_revision"] for x in later["elements"]+later["relations"]}
    changed={i for i in old_revisions.keys()|new_revisions.keys() if old_revisions.get(i)!=new_revisions.get(i)}
    for item in later["elements"]+later["relations"]:
        if item["id"] not in reviewed_upserts and set(item["context_ids"]) & changed:
            raise KnowledgeFoundationError(f"V2 context changed without a fresh object review: {item['id']}")
    validate_projection(later,root=root,resolve=True)
    return later


def query(projection: dict[str, Any], *, term: str = "", package_id: str | None = None, role: str | None = None) -> dict[str, Any]:
    elements={x["id"]:x for x in projection["elements"]}; relations={x["id"]:x for x in projection["relations"]}
    needle=term.casefold().strip()
    chosen=[p for p in projection["packages"] if (package_id is None or p["id"]==package_id) and (role is None or any(elements[i]["kind"]==role for i in p["element_ids"])) and (not needle or needle in " ".join([p["summary"],*[elements[i]["text"] for i in p["element_ids"]]]).casefold())]
    initial={i for p in chosen for i in p["element_ids"]}
    edge_ids={i for p in chosen for i in p["relation_ids"]}
    edge_ids.update(r["id"] for r in relations.values() if r["from_id"] in initial or r["to_id"] in initial)
    closure=initial|{endpoint for i in edge_ids for endpoint in (relations[i]["from_id"],relations[i]["to_id"])}
    locators={a["locator"] for i in closure for a in [*elements[i]["anchors"], *elements[i].get("regions", [])]}
    gaps=[x for x in projection["information"] if x["status"] in ("gap","uncertain","not_stated") and x["locator"] in locators]
    package_refs=[{"id":p["id"],"summary":p["summary"]} for p in projection["packages"] if set(p["element_ids"])&closure]
    return {"schema_version":VERSION,"artifact_id":projection["artifact_id"],"revision_id":projection["revision"]["id"],"profile":projection["profile"],"source":{k:projection["source"][k] for k in ("id","sha256","snapshot_sha256")},"packages":deepcopy(chosen),"package_refs":package_refs,"elements":[deepcopy(elements[i]) for i in sorted(closure)],"relations":[deepcopy(relations[i]) for i in sorted(edge_ids)],"gaps":deepcopy(gaps),"gap_summary":dict(Counter(x["status"] for x in projection["information"])),"answer_gap":None if chosen else "No reviewed package in this revision answers the selection; inspect source information before concluding the source is silent."}


def render_markdown(projection: dict[str, Any]) -> str:
    elements={x["id"]:x for x in projection["elements"]}; relations={x["id"]:x for x in projection["relations"]}
    lines=[f"# {projection['source']['title']} — 来源语义视图","",f"发布 `{projection['revision']['id']}` · 规范 `{projection['profile']['id']}@{projection['profile']['version']}` · 来源 `{projection['source']['sha256']}`","","来源核验与语义审阅不评估科学有效性或个人适用性。","","## 内容包总览",""]
    for package in projection["packages"]:
        lines.append(f"- `{package['id']}`：{package['summary']}")
    for package in projection["packages"]:
        lines.extend(["",f"## {package['summary']} (`{package['id']}`)",""])
        for element_id in package["element_ids"]:
            item=elements[element_id]
            quantity=item.get("quantity")
            amount=f"；量值 {quantity['amount']} {quantity['unit']}（基准 {quantity['basis']}；范围 {quantity['scope']}）" if quantity else ""
            anchor=", ".join(f"`{a['locator']}[{a['start']}:{a['end']}]`" for a in item["anchors"])
            if item.get("regions"):
                anchor += "; 原页区域 " + ", ".join(f"`{r['locator']} bbox={r['bbox']} {r['native_kind']} {r['interpretation_status']}`" for r in item["regions"])
            lines.append(f"- {item['kind']} `{item['id']}`（{item['origin_type']} / {item['modality']} / {item['review_state']}）：{item['text']}{amount}；依据 {anchor}。")
        lines.extend(["","关系："])
        for relation_id in package["relation_ids"]:
            item=relations[relation_id]
            anchor=", ".join(f"`{a['locator']}[{a['start']}:{a['end']}]`" for a in item["anchors"])
            if item.get("regions"):
                anchor += "; 原页区域 " + ", ".join(f"`{r['locator']} bbox={r['bbox']} {r['native_kind']} {r['interpretation_status']}`" for r in item["regions"])
            lines.append(f"- `{item['id']}`：`{item['from_id']}` —{item['type']}→ `{item['to_id']}`；依据 {anchor}。{item['note']}")
    lines.extend(["","## 关系图","","```mermaid","flowchart LR"])
    aliases={item["id"]:f"n{i}" for i,item in enumerate(projection["elements"])}
    for item in projection["elements"]:
        lines.append(f"    {aliases[item['id']]}[\"{item['kind']}: {item['id']}\"]")
    for item in projection["relations"]:
        lines.append(f"    {aliases[item['from_id']]} -->|{item['type']}| {aliases[item['to_id']]}")
    lines.extend(["```","","## 信息缺口",""])
    for item in projection["information"]:
        if item["status"] in ("gap","uncertain","not_stated"):
            lines.append(f"- `{item['id']}` `{item['locator']}[{item['start']}:{item['end']}]`：{item['status']}；{item['meaning']}；{item['reason']}")
    return "\n".join(lines).rstrip()+"\n"


def export(projection: dict[str, Any], *, root: Path, producer_commit: str) -> dict[str, Any]:
    validate_projection(projection,root=root,resolve=True)
    if not producer_commit.strip():
        raise KnowledgeFoundationError("V2 export requires producer commit or release.")
    return {"schema_version":EXPORT_VERSION,"producer":{"name":"source-knowledge-modeling","commit_or_release":producer_commit.strip()},"artifact_schema_version":VERSION,"artifact_id":projection["artifact_id"],"artifact_revision_id":projection["revision"]["id"],"profile":deepcopy(projection["profile"]),"source":deepcopy(projection["source"]),"information":deepcopy(projection["information"]),"elements":deepcopy(projection["elements"]),"relations":deepcopy(projection["relations"]),"packages":deepcopy(projection["packages"]),"source_checks":deepcopy(projection["source_checks"]),"reviews":deepcopy(projection["reviews"]),"lineage":deepcopy(projection["lineage"])}


def impact(old: dict[str, Any], new: dict[str, Any], *, foundation: dict[str, Any] | None = None) -> dict[str, Any]:
    if old["artifact_id"]!=new["artifact_id"] or new["revision"]["parent_id"]!=old["revision"]["id"]:
        raise KnowledgeFoundationError("V2 impact needs consecutive revisions of the same artifact.")
    profile_changed=old["profile"]!=new["profile"]
    source_changed=old["source"]["sha256"]!=new["source"]["sha256"] or old["source"]["snapshot_sha256"]!=new["source"]["snapshot_sha256"]
    def changed(key: str, value_key: str | None = None) -> list[str]:
        before={x["id"]:x[value_key] if value_key else x for x in old[key]}; after={x["id"]:x[value_key] if value_key else x for x in new[key]}
        return sorted(i for i in before.keys()|after.keys() if before.get(i)!=after.get(i))
    changed_elements=changed("elements","content_revision"); changed_relations=changed("relations","content_revision"); changed_packages=changed("packages")
    rescreen=list(new["scope"]["included_locators"]) if profile_changed or source_changed else []
    changed_ids=set(changed_elements+changed_relations)
    dependent={x["id"] for x in new["elements"]+new["relations"] if set(x["context_ids"]) & changed_ids}
    return {"from_revision":old["revision"]["id"],"to_revision":new["revision"]["id"],"profile_changed":profile_changed,"source_changed":source_changed,"anchor_mapping_required":source_changed,"rescreen_locators":rescreen,"changed_element_ids":changed_elements,"changed_relation_ids":changed_relations,"changed_package_ids":changed_packages,"dependent_object_ids":sorted(dependent),"recheck_object_ids":sorted(changed_ids|dependent|({x["id"] for x in new["elements"]+new["relations"]} if profile_changed or source_changed else set())),"rebuild_views":bool(changed_elements or changed_relations or changed_packages or profile_changed or source_changed),"rebuild_export":bool(changed_elements or changed_relations or changed_packages or profile_changed or source_changed),"reuse_element_count":len({x["id"]:x["content_revision"] for x in old["elements"]}.items()&{x["id"]:x["content_revision"] for x in new["elements"]}.items())}


def impact_migration(old: dict[str, Any], new: dict[str, Any], old_bytes: bytes) -> dict[str, Any]:
    if old.get("schema_version") != "knowledge-semantic-projection/v1" or new.get("schema_version") != VERSION or new["legacy"] != {"schema_version": old["schema_version"], "artifact_id": old["artifact_id"], "revision_id": old["revision"]["id"], "sha256": sha256(old_bytes).hexdigest()}:
        raise KnowledgeFoundationError("V2 migration impact identity mismatch.")
    links = [x for x in new["lineage"] if x["change"] == "migrated"]
    return {"from_revision": old["revision"]["id"], "to_revision": new["revision"]["id"], "format_changed": True,
            "source_reused": old["source"]["sha256"] == new["source"]["sha256"] and old["source"]["snapshot_sha256"] == new["source"]["snapshot_sha256"],
            "mapped_old_statements": sum(x["old_kind"] == "statement" for x in links), "mapped_old_elements": sum(x["old_kind"] == "element" for x in links), "mapped_old_relations": sum(x["old_kind"] == "relation" for x in links),
            "historical_proposal_reviews": sum(p["legacy_review"] is not None for p in new["packages"]), "new_acceptance_events": len(new["reviews"]),
            "information_status": dict(Counter(x["status"] for x in new["information"])), "unmapped_old_ids": [x["old_id"] for x in new["lineage"] if not x["new_ids"]]}


def impact_view(old_spec: dict[str, Any], new_spec: dict[str, Any], projection: dict[str, Any], artifact_bytes: bytes) -> dict[str, Any]:
    expected = sha256(artifact_bytes).hexdigest()
    for spec in (old_spec, new_spec):
        if spec.get("schema_version") != VIEW_VERSION or spec.get("artifact_id") != projection["artifact_id"] or spec.get("revision_id") != projection["revision"]["id"] or spec.get("artifact_sha256") != expected:
            raise KnowledgeFoundationError("View impact requires two specs of the exact same v2 projection bytes.")
    return {"revision_id": projection["revision"]["id"], "artifact_sha256": expected, "view_changed": old_spec != new_spec,
            "knowledge_reused": True, "semantic_reviews_reused": True, "rebuild_views": ["offline_html"] if old_spec != new_spec else [],
            "changed_groups": old_spec.get("groups") != new_spec.get("groups"), "changed_labels": old_spec.get("package_labels") != new_spec.get("package_labels") or old_spec.get("title") != new_spec.get("title")}
