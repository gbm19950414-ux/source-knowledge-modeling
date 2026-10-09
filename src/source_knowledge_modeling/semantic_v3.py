"""Ordered, source-linked semantic and discourse projection v3.

The author/writer supplies judgments. This module resolves locations and revisions,
checks referential contracts, and records independent source and semantic reviews.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .core import KnowledgeFoundationError, load_json, validate as validate_foundation
from .semantic import anchor_key, profile_manifest
from . import semantic_v2 as v2

VERSION = "knowledge-semantic-projection/v3"
DRAFT_VERSION = "knowledge-semantic-drafts/v3"
REVIEW_VERSION = "knowledge-semantic-review/v3"
SOURCE_CHECK_VERSION = v2.SOURCE_CHECK_VERSION
EXPORT_VERSION = "source-knowledge-export/v4"
_CONTENT_KEYS = {
    "element": ("kind", "text", "origin_type", "modality", "negated", "example_scope", "quantity", "anchors", "regions", "context_ids", "legacy_fields"),
    "relation": ("type", "from_id", "to_id", "from_revision", "to_revision", "origin_type", "basis", "premises", "rationale", "uncertainty", "note", "anchors", "regions", "context_ids", "legacy_fields"),
}
_RELATION_SOURCE_KINDS = {"conditions": "condition", "exception_to": "exception", "replaces": "action", "precedes": "action", "quantifies": "parameter", "example_of": "example"}
_SEMANTIC_BINDING_ROOTS = {
    "element": {"kind", "text", "origin_type", "modality", "negated", "example_scope", "quantity"},
    "relation": {"type", "from_id", "to_id", "origin_type", "basis", "premises", "rationale", "uncertainty", "note"},
}


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


def region_key(region: dict[str, Any]) -> str:
    return digest(region)


def _schema(name: str) -> dict[str, Any]:
    return load_json(Path(__file__).resolve().parent / "schemas" / name)


def _check_schema(value: dict[str, Any], name: str) -> None:
    errors = sorted(Draft202012Validator(_schema(name)).iter_errors(value), key=lambda error: str(error.path))
    if errors:
        error = errors[0]
        raise KnowledgeFoundationError(f"V3 {name} error at {list(error.path)}: {error.message}")


def _unique(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    ids = [x["id"] for x in items]
    if len(ids) != len(set(ids)):
        raise KnowledgeFoundationError(f"Duplicate v3 {label} ID.")
    return {x["id"]: x for x in items}


def _source_units(projection: dict[str, Any], root: Path, *, verify: bool) -> dict[str, dict[str, Any]]:
    source = projection["source"]
    try:
        snapshot_bytes = (root / source["snapshot_path"]).read_bytes()
    except OSError as exc:
        raise KnowledgeFoundationError(f"V3 source unavailable: {root / source['snapshot_path']}") from exc
    if verify:
        for name, hash_name in (("path", "sha256"), ("snapshot_path", "snapshot_sha256")):
            try:
                data = snapshot_bytes if name == "snapshot_path" else (root / source[name]).read_bytes()
                actual = sha256(data).hexdigest()
            except OSError as exc:
                raise KnowledgeFoundationError(f"V3 source unavailable: {root / source[name]}") from exc
            if actual != source[hash_name]:
                raise KnowledgeFoundationError(f"V3 source hash mismatch: {source[name]}")
    # Parse the bytes just verified, rather than opening a mutable file again.
    snapshot = json.loads(snapshot_bytes)
    units = snapshot.get("units")
    if not isinstance(units, list):
        raise KnowledgeFoundationError("V3 source snapshot requires units.")
    if len({x.get("locator") for x in units}) != len(units):
        raise KnowledgeFoundationError("V3 snapshot locators must be unique.")
    return {x["locator"]: x for x in units}


def _anchor(anchor: dict[str, Any], locators: set[str], units: dict[str, dict[str, Any]] | None) -> None:
    if anchor["locator"] not in locators or anchor["end"] <= anchor["start"]:
        raise KnowledgeFoundationError(f"V3 anchor outside evidence scope: {anchor}")
    if units is not None and units[anchor["locator"]]["text"][anchor["start"]:anchor["end"]] != anchor["quote"]:
        raise KnowledgeFoundationError(f"V3 anchor quote mismatch: {anchor['locator']}")


def _region(region: dict[str, Any], locators: set[str], units: dict[str, dict[str, Any]] | None) -> None:
    v2._check_region(region, locators, units)


def _pointer_value(obj: dict[str, Any], path: str, kind: str) -> Any:
    if not path.startswith("/"):
        raise KnowledgeFoundationError(f"V3 binding requires JSON Pointer: {path}")
    tokens = [x.replace("~1", "/").replace("~0", "~") for x in path[1:].split("/")]
    # An interpreted visual meaning can live here once; evidence identity fields
    # alone never represent the meaning claimed by an audit.
    if re.fullmatch(r"/regions/(0|[1-9][0-9]*)/note", path):
        try:
            region = obj["regions"][int(tokens[1])]
            note = region["note"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise KnowledgeFoundationError(f"V3 binding field absent: {path}") from exc
        if region.get("interpretation_status") != "interpreted" or not isinstance(note, str) or not note.strip():
            raise KnowledgeFoundationError(f"V3 binding requires an interpreted, nonempty visual note: {path}")
        return note
    if not tokens or tokens[0] not in _SEMANTIC_BINDING_ROOTS[kind]:
        raise KnowledgeFoundationError(f"V3 binding is not a semantic field: {path}")
    current: Any = obj
    try:
        for token in tokens:
            if isinstance(current, list):
                current = current[int(token)]
            else:
                current = current[token]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise KnowledgeFoundationError(f"V3 binding field absent: {path}") from exc
    return current


def _check_audits(items: list[dict[str, Any]], label: str, primary: set[str], evidence: set[str], objects: dict[str, dict[str, Any]], kinds: dict[str, str], units: dict[str, dict[str, Any]] | None = None) -> None:
    _unique(items, label)
    for item in items:
        if item["locator"] not in primary:
            raise KnowledgeFoundationError(f"V3 {label} outside primary scope: {item['id']}")
        if "region" in item:
            if item["region"]["locator"] != item["locator"]:
                raise KnowledgeFoundationError(f"V3 {label} region locator mismatch: {item['id']}")
            _region(item["region"], primary, units)
            if item["status"] == "modeled" and item["region"]["interpretation_status"] != "interpreted":
                raise KnowledgeFoundationError(f"V3 modeled {label} has unresolved visual meaning: {item['id']}")
        elif (item["start"] < 0 or item["end"] <= item["start"]
              or units is not None and item["end"] > len(units[item["locator"]].get("text", ""))):
            raise KnowledgeFoundationError(f"V3 {label} invalid text range: {item['id']}")
        if item["status"] == "modeled" and not item["bindings"]:
            raise KnowledgeFoundationError(f"V3 modeled {label} lacks field bindings: {item['id']}")
        if item["status"] != "modeled" and item["bindings"]:
            raise KnowledgeFoundationError(f"V3 non-modeled {label} cannot claim a field binding: {item['id']}")
        if item["status"] == "not_stated" and (not item.get("checked_scope") or not set(item["checked_scope"]) <= evidence):
            raise KnowledgeFoundationError(f"V3 not-stated {label} lacks checked scope: {item['id']}")
        for binding in item["bindings"]:
            obj = objects.get(binding["object_id"])
            if obj is None or obj["content_revision"] != binding["content_revision"]:
                raise KnowledgeFoundationError(f"V3 {label} binding revision mismatch: {item['id']}")
            _pointer_value(obj, binding["field_path"], kinds[binding["object_id"]])
        if item["status"] == "modeled" and "region" in item:
            region = item["region"]
            native_fields = ("locator", "bbox", "native_kind", "native_structure_sha256")
            if not any(all(r[k] == region[k] for k in native_fields)
                       for binding in item["bindings"]
                       for r in objects[binding["object_id"]].get("regions", [])):
                raise KnowledgeFoundationError(f"V3 visual {label} lacks matching bound object evidence: {item['id']}")


def validate_projection(projection: dict[str, Any], *, root: Path | None = None, resolve: bool = False) -> dict[str, Any]:
    _check_schema(projection, "knowledge-semantic-projection-v3.schema.json")
    if projection["profile"] != profile_manifest(projection["profile"]["id"], projection["profile"]["version"]):
        raise KnowledgeFoundationError("V3 profile definition hash mismatch.")
    if resolve and root is None:
        raise KnowledgeFoundationError("V3 resolved validation needs source root.")
    units = _source_units(projection, root, verify=True) if resolve and root is not None else None
    primary = projection["scope"]["included_locators"]
    context = projection["scope"].get("context_locators", [])
    if not primary or len(set(primary)) != len(primary) or len(set(context)) != len(context) or set(primary) & set(context):
        raise KnowledgeFoundationError("V3 primary/context scope must be distinct and unique.")
    evidence = set(primary) | set(context)
    source_units = projection["source_units"]
    if {x["locator"] for x in source_units} != evidence or len(source_units) != len(evidence) or len({x["order"] for x in source_units}) != len(source_units):
        raise KnowledgeFoundationError("V3 source units do not cover the declared scope once.")
    if source_units != sorted(source_units, key=lambda x: x["order"]):
        raise KnowledgeFoundationError("V3 source units must follow foundation reading order.")
    if any(x["scope_role"] != ("primary" if x["locator"] in primary else "context") for x in source_units):
        raise KnowledgeFoundationError("V3 source unit scope role mismatch.")
    if units is not None and not evidence <= set(units):
        raise KnowledgeFoundationError("V3 scope locator absent from snapshot.")
    screening = projection["screening"]
    if len(screening) != len(primary) or {x["locator"] for x in screening} != set(primary):
        raise KnowledgeFoundationError("V3 screening must cover primary locators once.")
    elements = _unique(projection["elements"], "element")
    relations = _unique(projection["relations"], "relation")
    packages = _unique(projection["packages"], "package")
    checks = _unique(projection["source_checks"], "source check")
    reviews = _unique(projection["reviews"], "review")
    for check in checks.values():
        if (check["source_sha256"] != projection["source"]["sha256"]
                or check["snapshot_sha256"] != projection["source"]["snapshot_sha256"]
                or projection["source"]["kind"] == "pdf" and check["check_method"] != "original_visual"):
            raise KnowledgeFoundationError(f"V3 source check has wrong source or method: {check['id']}")
    if set(elements) & set(relations):
        raise KnowledgeFoundationError("V3 element/relation IDs share one namespace.")
    objects = {**elements, **relations}
    kinds = {**{x: "element" for x in elements}, **{x: "relation" for x in relations}}
    for item in [*elements.values(), *relations.values(), *packages.values()]:
        if not set(item["context_ids"]) <= set(objects) or item["id"] in item["context_ids"]:
            raise KnowledgeFoundationError(f"V3 context dependency absent/self-referential: {item['id']}")
    for item in objects.values():
        kind = kinds[item["id"]]
        if item["content_revision"] != content_revision(item, kind):
            raise KnowledgeFoundationError(f"V3 content revision mismatch: {item['id']}")
        for span in item["anchors"]:
            _anchor(span, evidence, units)
        for region in item.get("regions", []):
            _region(region, evidence, units)
        state = item["review_state"]
        if state == "accepted":
            review = reviews.get(item["review_id"])
            if not review or not item["source_check_ids"] or not set(item["source_check_ids"]) <= set(checks):
                raise KnowledgeFoundationError(f"V3 accepted object lacks independent check/review: {item['id']}")
            if projection["source"]["kind"] == "pdf" and review["check_method"] != "original_visual":
                raise KnowledgeFoundationError(f"V3 PDF review must use original visual: {item['id']}")
            required = {anchor_key(a) for a in item["anchors"]}
            required_regions = {region_key(a) for a in item.get("regions", [])}
            if not required <= set(review["checked_anchor_keys"]) or not required_regions <= set(review.get("checked_region_keys", [])):
                raise KnowledgeFoundationError(f"V3 review does not cover object: {item['id']}")
            checked = {k for cid in item["source_check_ids"] for k in checks[cid]["anchor_keys"]}
            checked_regions = {k for cid in item["source_check_ids"] for k in checks[cid].get("region_keys", [])}
            if not required <= checked or not required_regions <= checked_regions:
                raise KnowledgeFoundationError(f"V3 source check does not cover object: {item['id']}")
        elif item["review_id"] is not None:
            raise KnowledgeFoundationError(f"V3 pending/historical object cannot claim current acceptance: {item['id']}")
    for element in elements.values():
        if (element["kind"] == "parameter") != ("quantity" in element):
            raise KnowledgeFoundationError(f"V3 quantity assignment mismatch: {element['id']}")
        if element["kind"] == "parameter" and not any(r["type"] == "quantifies" and r["from_id"] == element["id"] for r in relations.values()):
            raise KnowledgeFoundationError(f"V3 parameter lacks target: {element['id']}")
    for relation in relations.values():
        source, target = elements.get(relation["from_id"]), elements.get(relation["to_id"])
        if source is None or target is None or source is target or relation["from_revision"] != source["content_revision"] or relation["to_revision"] != target["content_revision"]:
            raise KnowledgeFoundationError(f"V3 relation endpoints/revisions mismatch: {relation['id']}")
        required = _RELATION_SOURCE_KINDS.get(relation["type"])
        if required and source["kind"] != required:
            raise KnowledgeFoundationError(f"V3 relation source-kind mismatch: {relation['id']}")
        if relation["type"] in ("precedes", "replaces", "exception_to") and target["kind"] not in (("action", "assertion") if relation["type"] == "exception_to" else ("action",)):
            raise KnowledgeFoundationError(f"V3 relation target-kind mismatch: {relation['id']}")
        if relation["type"] == "conditions" and target["kind"] not in ("action", "assertion"):
            raise KnowledgeFoundationError(f"V3 condition target-kind mismatch: {relation['id']}")
        if relation["type"] == "quantifies" and target["kind"] not in ("action", "assertion", "example", "exception"):
            raise KnowledgeFoundationError(f"V3 quantity target-kind mismatch: {relation['id']}")
        if relation["type"] == "contrasts_with" and relation["from_id"] > relation["to_id"]:
            raise KnowledgeFoundationError(f"V3 symmetric contrast endpoints must be canonical: {relation['id']}")
        if relation["type"] == "exception_to" and relation["basis"] != "explicit" and relation["review_state"] != "legacy_historical":
            raise KnowledgeFoundationError(f"V3 default-rule exception must be explicit: {relation['id']}")
        if relation["origin_type"] == "project_interpretation":
            if relation["basis"] != "project_interpretation" or not relation["premises"] or not relation["rationale"].strip() or not relation["uncertainty"].strip() or not set(relation["premises"]) <= set(relation["context_ids"]):
                raise KnowledgeFoundationError(f"V3 interpretation lacks premise/reason/uncertainty: {relation['id']}")
        elif relation["origin_type"] == "unresolved":
            if relation["basis"] != "unresolved":
                raise KnowledgeFoundationError(f"V3 unresolved relation basis mismatch: {relation['id']}")
        elif relation["basis"] not in ("explicit", "inferred_from_source") or relation["basis"] == "inferred_from_source" and not relation["rationale"].strip():
            raise KnowledgeFoundationError(f"V3 source relation lacks a defensible basis: {relation['id']}")
    successors: dict[str, list[str]] = {}
    for relation in relations.values():
        if relation["type"] == "precedes":
            successors.setdefault(relation["from_id"], []).append(relation["to_id"])
    active: set[str] = set(); visited: set[str] = set()
    def walk(node: str) -> None:
        if node in active:
            raise KnowledgeFoundationError("V3 cyclic step order.")
        if node in visited:
            return
        active.add(node)
        for next_id in successors.get(node, []):
            walk(next_id)
        active.remove(node); visited.add(node)
    for node in successors:
        walk(node)
    referenced_e: set[str] = set(); referenced_r: set[str] = set()
    for package in packages.values():
        if not set(package["element_ids"]) <= set(elements) or not set(package["relation_ids"]) <= set(relations):
            unknown_e = sorted(set(package["element_ids"]) - set(elements))
            unknown_r = sorted(set(package["relation_ids"]) - set(relations))
            raise KnowledgeFoundationError(
                f"V3 package references missing objects: {package['id']}; "
                f"element_ids={unknown_e}; relation_ids={unknown_r}.")
        referenced_e.update(package["element_ids"]); referenced_r.update(package["relation_ids"])
    if referenced_e != set(elements) or referenced_r != set(relations):
        raise KnowledgeFoundationError(
            "V3 objects need at least one package reference. "
            f"Unreferenced element_ids={sorted(set(elements) - referenced_e)}; "
            f"relation_ids={sorted(set(relations) - referenced_r)}. "
            "Assign each existing object to an appropriate package's element_ids or relation_ids; "
            "context_ids do not establish package membership. Do not invent or change source meaning.")
    occurrences = _unique(projection["occurrences"], "occurrence")
    seen_e: set[str] = set()
    for occurrence in occurrences.values():
        element = elements.get(occurrence["element_id"])
        if element is None or occurrence["locator"] not in evidence or not any(a["locator"] == occurrence["locator"] and a["start"] == occurrence["start"] and a["end"] == occurrence["end"] for a in element["anchors"]):
            raise KnowledgeFoundationError(f"V3 occurrence has no matching element evidence: {occurrence['id']}")
        seen_e.add(occurrence["element_id"])
    if seen_e != set(elements):
        raise KnowledgeFoundationError("V3 every element needs a source occurrence.")
    source_order = {unit["locator"]: unit["order"] for unit in source_units}
    if projection["occurrences"] != sorted(projection["occurrences"],
                                           key=lambda x: (source_order[x["locator"]], x["start"], x["end"], x["element_id"], x["id"])):
        raise KnowledgeFoundationError("V3 occurrences must follow source reading and character order.")
    _check_audits(projection["information"], "information", set(primary), evidence, objects, kinds, units)
    _check_audits(projection["discourse"], "discourse", set(primary), evidence, objects, kinds, units)
    return {"revision_id": projection["revision"]["id"], "element_count": len(elements), "relation_count": len(relations), "package_count": len(packages), "information_status": dict(Counter(x["status"] for x in projection["information"])), "discourse_status": dict(Counter(x["status"] for x in projection["discourse"]))}


def new_projection(foundation: dict[str, Any], foundation_bytes: bytes, scope: dict[str, Any],
                   profile: dict[str, str], *, revision_id: str = "base", root: Path | None = None) -> dict[str, Any]:
    """Start a v3 revision directly from a frozen foundation, without a v1 bridge."""
    validate_foundation(foundation, root=root, resolve=root is not None)
    if profile != profile_manifest(profile["id"], profile["version"]) or profile["version"] != "3.0.0":
        raise KnowledgeFoundationError("V3 prepare needs the installed instructional-source@3.0.0 profile.")
    primary, context = scope.get("included_locators"), scope.get("context_locators", [])
    known = {x["locator"] for x in foundation["units"]}
    if (not isinstance(primary, list) or not primary or not isinstance(context, list)
            or len(primary) != len(set(primary)) or len(context) != len(set(context))
            or set(primary) & set(context) or not set(primary + context) <= known
            or not isinstance(scope.get("description"), str)):
        raise KnowledgeFoundationError("V3 scope needs distinct, known primary and context locators.")
    ordered = sorted((x for x in foundation["units"] if x["locator"] in set(primary + context)), key=lambda x: x["order"])
    result = {"schema_version": VERSION, "artifact_id": foundation["artifact_id"],
              "revision": {"id": revision_id, "parent_id": None, "note": "Fresh v3 source workspace."},
              "profile": deepcopy(profile), "source": deepcopy(foundation["source"]), "legacy": None,
              "scope": deepcopy(scope), "screening": [{"locator": x["locator"], "status": "unmodeled", "reason": "Awaiting source-first audit."} for x in ordered if x["locator"] in primary],
              "information": [], "discourse": [], "elements": [], "relations": [], "packages": [],
              "source_checks": [], "reviews": [], "lineage": [], "occurrences": [],
              "source_units": [{"locator": x["locator"], "order": x["order"],
                                "scope_role": "primary" if x["locator"] in primary else "context"} for x in ordered]}
    validate_projection(result, root=root, resolve=root is not None)
    return result


def migrate_v2(old: dict[str, Any], old_bytes: bytes, *, root: Path,
               foundation: dict[str, Any], revision_id: str) -> dict[str, Any]:
    """Retain v2 identities and review history while making unreviewed inheritance explicit."""
    v2.validate_projection(old, root=root, resolve=True)
    validate_foundation(foundation, root=root, resolve=True)
    if old["source"] != foundation["source"]:
        raise KnowledgeFoundationError("V3 migration needs the exact old source; map changed locators separately.")
    base = new_projection(foundation, canonical_bytes(foundation), old["scope"],
                          profile_manifest("instructional-source", "3.0.0"), revision_id=revision_id, root=root)
    base["revision"] = {"id": revision_id, "parent_id": old["revision"]["id"],
                        "note": "Historical v2 objects require new individual semantic review."}
    base["legacy"] = {"schema_version": old["schema_version"], "artifact_id": old["artifact_id"],
                      "revision_id": old["revision"]["id"], "sha256": sha256(old_bytes).hexdigest(),
                      "review_history": deepcopy(old["reviews"])}
    base["screening"] = deepcopy(old["screening"])
    base["source_checks"] = deepcopy(old["source_checks"])
    owner = {ident: p["id"] for p in old["packages"] for ident in p["element_ids"] + p["relation_ids"]}
    for source in old["elements"]:
        obj = deepcopy(source)
        obj["legacy_fields"] = {"origin_type": source["origin_type"], "modality": source["modality"],
                                "negated": source["negated"], "example_scope": source["example_scope"],
                                "content_revision": source["content_revision"], "review_state": source["review_state"],
                                "review_id": source["review_id"], "source_check_ids": deepcopy(source["source_check_ids"])}
        obj.update({"origin_type": "unresolved", "modality": "unresolved", "negated": False,
                    "example_scope": None, "source_check_ids": [], "review_state": "legacy_historical",
                    "review_id": None, "legacy_review_package_id": owner.get(source["id"]),
                    "prior_review_id": source["review_id"]})
        base["elements"].append(seal(obj, "element"))
    element_revisions = {x["id"]: x["content_revision"] for x in base["elements"]}
    for source in old["relations"]:
        obj = deepcopy(source)
        obj["legacy_fields"] = {"content_revision": source["content_revision"],
                                "review_state": source["review_state"], "review_id": source["review_id"],
                                "source_check_ids": deepcopy(source["source_check_ids"])}
        obj.update({"from_revision": element_revisions[source["from_id"]],
                    "to_revision": element_revisions[source["to_id"]], "origin_type": "unresolved",
                    "basis": "unresolved", "premises": [], "rationale": "", "uncertainty": "",
                    "source_check_ids": [], "review_state": "legacy_historical", "review_id": None,
                    "legacy_review_package_id": owner.get(source["id"]), "prior_review_id": source["review_id"]})
        base["relations"].append(seal(obj, "relation"))
    base["packages"] = deepcopy(old["packages"])
    base["lineage"] = deepcopy(old["lineage"])
    for source in old["information"]:
        base["information"].append({"id": source["id"], "locator": source["locator"],
                                    "start": source["start"], "end": source["end"], "meaning": source["meaning"],
                                    "status": "uncertain" if source["status"] == "modeled" else source["status"],
                                    "bindings": [], "reason": "V2 model_refs do not prove a v3 semantic field; prior refs: " + ", ".join(source["model_refs"])
                                    if source["status"] == "modeled" else source["reason"],
                                    **({"checked_scope": source["checked_scope"]} if "checked_scope" in source else {})})
    base["occurrences"] = _occurrences(base["elements"], base["source_units"])
    validate_projection(base, root=root, resolve=True)
    return base


def resolve_anchor(base: dict[str, Any], anchor: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Resolve an authoring quote only when its location in one unit is unambiguous."""
    return anchor_resolver(base, root=root)(anchor)


def anchor_resolver(base: dict[str, Any], *, root: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Verify once for this operation; a new operation always rereads its sources."""
    units = _source_units(base, root, verify=True)
    evidence = set(base["scope"]["included_locators"]) | set(base["scope"].get("context_locators", []))
    return lambda anchor: _resolve_anchor(anchor, evidence, units)


def region_resolver(base: dict[str, Any], *, root: Path) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Resolve native visual evidence against the exact frozen source, without OCR quotes."""
    units = _source_units(base, root, verify=True)
    evidence = set(base["scope"]["included_locators"]) | set(base["scope"].get("context_locators", []))
    schema = _schema("knowledge-semantic-projection-v3.schema.json")
    validator = Draft202012Validator({"$ref": "#/$defs/region", "$defs": schema["$defs"]})

    def resolve(region: dict[str, Any]) -> dict[str, Any]:
        errors = list(validator.iter_errors(region))
        if errors:
            raise KnowledgeFoundationError(f"V3 visual evidence schema mismatch: {errors[0].message}")
        _region(region, evidence, units)
        return deepcopy(region)

    return resolve


def _resolve_anchor(anchor: dict[str, Any], evidence: set[str], units: dict[str, dict[str, Any]]) -> dict[str, Any]:
    locator = anchor.get("locator")
    if locator not in evidence:
        raise KnowledgeFoundationError(f"V3 anchor outside evidence scope: {locator}")
    text = units.get(locator, {}).get("text", "")
    quote = anchor.get("quote")
    if not isinstance(quote, str) or not quote:
        raise KnowledgeFoundationError("V3 source anchor needs an exact nonempty quote.")
    if "start" in anchor or "end" in anchor:
        start, end = anchor.get("start"), anchor.get("end")
        if (type(start) is not int or type(end) is not int or start < 0 or end <= start
                or end > len(text) or text[start:end] != quote):
            raise KnowledgeFoundationError(f"V3 explicit quote offset mismatch: {locator}")
    else:
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            raise KnowledgeFoundationError(f"V3 quote missing or ambiguous; specify exact offsets: {locator}")
        end = start + len(quote)
    return {"locator": locator, "start": start, "end": end, "quote": quote}


def _occurrences(elements: list[dict[str, Any]], source_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for element in elements:
        for index, anchor in enumerate(element["anchors"], 1):
            result.append({"id": f"occ:{element['id']}:{index}", "element_id": element["id"],
                           "locator": anchor["locator"], "start": anchor["start"], "end": anchor["end"]})
    source_order = {unit["locator"]: unit["order"] for unit in source_units}
    return sorted(result, key=lambda x: (source_order[x["locator"]], x["start"], x["end"], x["element_id"], x["id"]))


def normalize_drafts(base: dict[str, Any], authored: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Turn declaration-only input into a sealed candidate; never create a review."""
    validate_projection(base, root=root, resolve=True)
    if authored.get("schema_version") != DRAFT_VERSION or authored.get("artifact_id") != base["artifact_id"] or authored.get("base_revision_id") != base["revision"]["id"]:
        raise KnowledgeFoundationError("V3 authored draft identity mismatch.")
    proposals = authored.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise KnowledgeFoundationError("V3 drafts need at least one proposal.")
    result = {"schema_version": DRAFT_VERSION, "artifact_id": base["artifact_id"],
              "base_revision_id": base["revision"]["id"], "base_sha256": digest(base), "proposals": []}
    existing = {x["id"]: x for x in base["elements"]}
    existing_relations = {x["id"]: x for x in base["relations"]}
    resolve = anchor_resolver(base, root=root)
    for source in proposals:
        proposal = deepcopy(source)
        for field in ("upsert_elements", "upsert_relations", "upsert_packages", "upsert_information",
                      "upsert_discourse", "upsert_occurrences", "retire_elements", "retire_relations",
                      "retire_packages", "lineage", "rule_ids"):
            proposal.setdefault(field, [])
        proposal["upsert_elements"] = []
        for raw in source.get("upsert_elements", []):
            obj = deepcopy(raw)
            obj["anchors"] = [resolve(a) for a in obj["anchors"]]
            obj.setdefault("context_ids", [])
            obj.setdefault("negated", False)
            obj.setdefault("example_scope", None)
            obj.pop("content_revision", None)
            proposal["upsert_elements"].append(seal(obj, "element"))
        endpoint_revisions = {**{i: x["content_revision"] for i, x in existing.items()},
                              **{x["id"]: x["content_revision"] for x in proposal["upsert_elements"]}}
        proposal["upsert_relations"] = []
        for raw in source.get("upsert_relations", []):
            obj = deepcopy(raw)
            obj["anchors"] = [resolve(a) for a in obj["anchors"]]
            obj.setdefault("context_ids", [])
            obj.setdefault("premises", [])
            obj.setdefault("rationale", "")
            obj.setdefault("uncertainty", "")
            obj.setdefault("note", "")
            try:
                obj["from_revision"] = endpoint_revisions[obj["from_id"]]
                obj["to_revision"] = endpoint_revisions[obj["to_id"]]
            except KeyError as exc:
                raise KnowledgeFoundationError(f"V3 relation endpoint missing from candidate: {exc}") from exc
            obj.pop("content_revision", None)
            proposal["upsert_relations"].append(seal(obj, "relation"))
        object_revisions = {**endpoint_revisions,
                            **{x["id"]: x["content_revision"] for x in existing_relations.values()},
                            **{x["id"]: x["content_revision"] for x in proposal["upsert_relations"]}}
        for field in ("upsert_information", "upsert_discourse"):
            for item in proposal[field]:
                if "anchor" in item:
                    if "region" in item:
                        raise KnowledgeFoundationError("V3 audit location must be a text span or a native region, not both.")
                    location = resolve(item.pop("anchor"))
                    item.update({key: location[key] for key in ("locator", "start", "end")})
                elif "region" in item:
                    if "locator" in item and item["locator"] != item["region"]["locator"]:
                        raise KnowledgeFoundationError("V3 authored audit region locator mismatch.")
                    item["locator"] = item["region"]["locator"]
                item.setdefault("bindings", [])
                for binding in item["bindings"]:
                    try:
                        binding["content_revision"] = object_revisions[binding["object_id"]]
                    except KeyError as exc:
                        raise KnowledgeFoundationError(f"V3 audit binding object absent: {exc}") from exc
        proposal["upsert_occurrences"] = _occurrences(proposal["upsert_elements"], base["source_units"])
        result["proposals"].append(proposal)
        for ident in proposal["retire_elements"]:
            existing.pop(ident, None)
        for ident in proposal["retire_relations"]:
            existing_relations.pop(ident, None)
        existing.update({x["id"]: x for x in proposal["upsert_elements"]})
        existing_relations.update({x["id"]: x for x in proposal["upsert_relations"]})
    validate_drafts(base, result, root=root)
    return result


def _upsert_items(items: list[dict[str, Any]], replacements: list[dict[str, Any]], retired: list[str]) -> list[dict[str, Any]]:
    by_id = {x["id"]: deepcopy(x) for x in items}
    for ident in retired:
        if ident not in by_id:
            raise KnowledgeFoundationError(f"V3 cannot retire absent object: {ident}")
        del by_id[ident]
    for item in replacements:
        by_id[item["id"]] = deepcopy(item)
    return list(by_id.values())


def _screening_from_audits(candidate: dict[str, Any]) -> list[dict[str, str]]:
    """Summarize field-level coverage without treating evidence alone as coverage."""
    result = []
    for unit in candidate["source_units"]:
        if unit["scope_role"] != "primary":
            continue
        statuses = [item["status"] for name in ("information", "discourse")
                    for item in candidate[name] if item["locator"] == unit["locator"]]
        if not statuses:
            status, reason = "unmodeled", "No source-first audit item maps this primary unit."
        elif "gap" in statuses:
            status, reason = ("partial" if "modeled" in statuses else "unmodeled"), "A declared source meaning or connection remains a gap."
        elif "uncertain" in statuses:
            status, reason = "uncertain", "A source meaning or connection is explicitly unresolved."
        elif "modeled" in statuses:
            status, reason = "modeled", "Inventoried meanings and connections have field-level model bindings."
        else:
            status, reason = "not_applicable", "Source-first audit records no applicable model item in this unit."
        result.append({"locator": unit["locator"], "status": status, "reason": reason})
    return result


def candidate_preview(base: dict[str, Any], drafts: dict[str, Any], *, root: Path) -> dict[str, Any]:
    _check_schema(drafts, "knowledge-semantic-drafts-v3.schema.json")
    if drafts["base_sha256"] != digest(base) or drafts["artifact_id"] != base["artifact_id"] or drafts["base_revision_id"] != base["revision"]["id"]:
        raise KnowledgeFoundationError("V3 candidate base content changed.")
    candidate = deepcopy(base)
    for proposal in drafts["proposals"]:
        for key, retired in (("elements", "retire_elements"), ("relations", "retire_relations"), ("packages", "retire_packages")):
            replacements = deepcopy(proposal["upsert_" + key])
            if key in ("elements", "relations"):
                for item in replacements:
                    item.update({"source_check_ids": [], "review_state": "pending_semantic", "review_id": None,
                                 "legacy_review_package_id": None})
            candidate[key] = _upsert_items(candidate[key], replacements, proposal[retired])
        for key in ("information", "discourse"):
            candidate[key] = _upsert_items(candidate[key], proposal["upsert_" + key], [])
        touched = {x["id"] for x in proposal["upsert_elements"]} | set(proposal["retire_elements"])
        candidate["occurrences"] = _upsert_items(candidate["occurrences"], proposal["upsert_occurrences"],
                                                   [x["id"] for x in candidate["occurrences"] if x["element_id"] in touched])
        candidate["lineage"].extend(deepcopy(proposal["lineage"]))
    source_order = {unit["locator"]: unit["order"] for unit in candidate["source_units"]}
    candidate["occurrences"].sort(key=lambda x: (source_order[x["locator"]], x["start"], x["end"], x["element_id"], x["id"]))
    candidate["screening"] = _screening_from_audits(candidate)
    validate_projection(candidate, root=root, resolve=True)
    return candidate


def validate_drafts(base: dict[str, Any], drafts: dict[str, Any], *, root: Path) -> None:
    validate_projection(base, root=root, resolve=True)
    _check_schema(drafts, "knowledge-semantic-drafts-v3.schema.json")
    if drafts["base_sha256"] != digest(base) or drafts["artifact_id"] != base["artifact_id"] or drafts["base_revision_id"] != base["revision"]["id"]:
        raise KnowledgeFoundationError("V3 draft base content changed.")
    if len({p["id"] for p in drafts["proposals"]}) != len(drafts["proposals"]):
        raise KnowledgeFoundationError("V3 proposal IDs must be unique.")
    rules = set(load_json(Path(__file__).resolve().parent / "profiles" / "instructional-source-3.0.0.json")["rules"])
    previous_elements = {x["id"]: x for x in base["elements"]}
    previous_relations = {x["id"]: x for x in base["relations"]}
    touched: set[tuple[str, str]] = set()
    units = _source_units(base, root, verify=True)
    evidence = set(base["scope"]["included_locators"]) | set(base["scope"].get("context_locators", []))
    for proposal in drafts["proposals"]:
        if not proposal["reason"].strip() or not set(proposal["rule_ids"]) <= rules:
            raise KnowledgeFoundationError(f"V3 proposal reason/rules invalid: {proposal['id']}")
        this_touched = ({("element", x["id"]) for x in proposal["upsert_elements"]}
                        | {("element", x) for x in proposal["retire_elements"]}
                        | {("relation", x["id"]) for x in proposal["upsert_relations"]}
                        | {("relation", x) for x in proposal["retire_relations"]})
        if touched & this_touched:
            raise KnowledgeFoundationError(f"V3 one object cannot be changed in multiple proposals: {proposal['id']}")
        if not set(proposal["retire_elements"]) <= {x["id"] for x in base["elements"]} or not set(proposal["retire_relations"]) <= {x["id"] for x in base["relations"]}:
            raise KnowledgeFoundationError(f"V3 retirement must reference the base revision: {proposal['id']}")
        touched |= this_touched
        for key, kind in (("upsert_elements", "element"), ("upsert_relations", "relation")):
            for item in proposal[key]:
                if item["content_revision"] != content_revision(item, kind):
                    raise KnowledgeFoundationError(f"V3 draft object content revision mismatch: {item['id']}")
                for anchor in item["anchors"]:
                    _anchor(anchor, evidence, units)
        required_lineage = {("element", ident) for ident in proposal["retire_elements"]}
        required_lineage |= {("relation", ident) for ident in proposal["retire_relations"]}
        required_lineage |= {("element", x["id"]) for x in proposal["upsert_elements"]
                             if x["id"] in previous_elements and x["content_revision"] != previous_elements[x["id"]]["content_revision"]}
        required_lineage |= {("relation", x["id"]) for x in proposal["upsert_relations"]
                             if x["id"] in previous_relations and x["content_revision"] != previous_relations[x["id"]]["content_revision"]}
        if not required_lineage <= {(x["old_kind"], x["old_id"]) for x in proposal["lineage"]}:
            raise KnowledgeFoundationError(f"V3 changed or retired object lacks explicit lineage: {proposal['id']}")
        previous_elements = {**{i: x for i, x in previous_elements.items() if i not in proposal["retire_elements"]},
                             **{x["id"]: x for x in proposal["upsert_elements"]}}
        previous_relations = {**{i: x for i, x in previous_relations.items() if i not in proposal["retire_relations"]},
                              **{x["id"]: x for x in proposal["upsert_relations"]}}
    candidate_preview(base, drafts, root=root)


def _proposal_anchors(base: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    old = {x["id"]: x for x in base["elements"] + base["relations"]}
    return [a for item in [*proposal["upsert_elements"], *proposal["upsert_relations"],
                            *(old[i] for i in proposal["retire_elements"] + proposal["retire_relations"])]
            for a in item["anchors"]]


def _proposal_regions(base: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    old = {x["id"]: x for x in base["elements"] + base["relations"]}
    return [r for item in [*proposal["upsert_elements"], *proposal["upsert_relations"],
                            *(old[i] for i in proposal["retire_elements"] + proposal["retire_relations"])]
            for r in item.get("regions", [])]


def _dependency_closure(objects: list[dict[str, Any]], changed: set[str]) -> set[str]:
    """Invalidate declared semantic dependencies, including unchanged intermediates."""
    dependents: dict[str, set[str]] = {}
    for item in objects:
        dependencies = set(item["context_ids"]) | set(item.get("premises", []))
        dependencies.update(ident for ident in (item.get("from_id"), item.get("to_id")) if ident is not None)
        for dependency in dependencies:
            dependents.setdefault(dependency, set()).add(item["id"])
    affected = set(changed)
    pending = list(changed)
    while pending:
        for ident in dependents.get(pending.pop(), ()):
            if ident not in affected:
                affected.add(ident)
                pending.append(ident)
    return affected


def _review_scope(old: dict[str, Any], new: dict[str, Any], dependency_certificate: Any = None) -> tuple[set[str], set[str], list[str]]:
    """Share publication/impact invalidation; declared edges prove no completeness."""
    if dependency_certificate is not None:
        from .semantic_dependency_cert import plan_delta
        try:
            plan = plan_delta(old, new, dependency_certificate)
        except KnowledgeFoundationError:
            # Impact may still report the conservative legacy scope. Publication
            # validates its reuse binding separately and cannot use this fallback.
            dependency_certificate = None
        else:
            affected = set(plan["affected_object_ids"])
            changed = set(plan["changed_object_ids"])
            return affected, affected - changed, plan["fallback_reasons"]
    before = {x["id"]: x["content_revision"] for x in old["elements"] + old["relations"]}
    after = {x["id"]: x["content_revision"] for x in new["elements"] + new["relations"]}
    changed = {ident for ident in before.keys() | after.keys() if before.get(ident) != after.get(ident)}
    seeds = set(changed)
    reasons = [name for name in ("source", "profile", "scope", "source_units", "packages", "screening")
               if old[name] != new[name]]
    for name in ("information", "discourse"):
        prior = {x["id"]: x for x in old[name]}
        later = {x["id"]: x for x in new[name]}
        audit_changes = {ident for ident in prior.keys() | later.keys() if prior.get(ident) != later.get(ident)}
        if audit_changes:
            reasons.append(name)
            for ident in audit_changes:
                for item in (prior.get(ident, {}), later.get(ident, {})):
                    seeds.update(x["object_id"] for x in item.get("bindings", []))
    declared = _dependency_closure(new["elements"] + new["relations"], seeds) - changed
    if changed or reasons:
        reasons.append("semantic_dependency_completeness_unknown")
        return set(after), declared & set(after), reasons
    return set(), set(), []


def _dependency(base: dict[str, Any], proposal: dict[str, Any], quality_report: dict[str, Any]) -> str:
    old = {x["id"]: x["content_revision"] for x in base["elements"] + base["relations"]}
    pending = {x["id"]: x["content_revision"] for x in proposal["upsert_elements"] + proposal["upsert_relations"]}
    ids = {ident for item in proposal["upsert_elements"] + proposal["upsert_relations"]
           for ident in item["context_ids"] + item.get("premises", []) + [item.get("from_id"), item.get("to_id")]
           if ident is not None}
    return digest({"profile": base["profile"], "source": base["source"],
                   "quality": quality_report["report_sha256"], "rules": proposal["rule_ids"],
                   "dependencies": {ident: pending.get(ident, old.get(ident)) for ident in sorted(ids)}})


def new_source_check_ledger(base: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SOURCE_CHECK_VERSION, "artifact_id": base["artifact_id"],
            "source_sha256": base["source"]["sha256"], "snapshot_sha256": base["source"]["snapshot_sha256"], "checks": []}


def record_source_check(base: dict[str, Any], ledger: dict[str, Any], *, check_id: str,
                        anchors: list[dict[str, Any]], reviewer: str, method: str,
                        note: str, root: Path, regions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    validate_projection(base, root=root, resolve=True)
    if any(ledger.get(k) != v for k, v in new_source_check_ledger(base).items() if k != "checks"):
        raise KnowledgeFoundationError("V3 source-check ledger identity mismatch.")
    if (not check_id or not reviewer.strip() or not (anchors or regions) or method not in ("original_visual", "source_text")
            or base["source"]["kind"] == "pdf" and method != "original_visual"):
        raise KnowledgeFoundationError("V3 source verification needs actual reviewer, method and evidence.")
    evidence = set(base["scope"]["included_locators"]) | set(base["scope"].get("context_locators", []))
    units = _source_units(base, root, verify=True)
    resolved = [_resolve_anchor(a, evidence, units) for a in anchors]
    for region in regions or []:
        _region(region, evidence, units)
    details = {"id": check_id, "source_sha256": base["source"]["sha256"],
               "snapshot_sha256": base["source"]["snapshot_sha256"],
               "anchor_keys": sorted({anchor_key(a) for a in resolved}),
               "region_keys": sorted({region_key(r) for r in regions or []}),
               "check_method": method, "reviewer": reviewer.strip(), "note": note}
    prior = next((x for x in ledger["checks"] if x["id"] == check_id), None)
    if prior is not None:
        if all(prior.get(key) == value for key, value in details.items()):
            return deepcopy(ledger)
        raise KnowledgeFoundationError("V3 source-check ID already records a different inspection.")
    later = deepcopy(ledger)
    later["checks"].append({**details, "checked_at": datetime.now(timezone.utc).isoformat()})
    return later


def new_review_ledger(base: dict[str, Any], drafts: dict[str, Any], quality_report: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": REVIEW_VERSION, "artifact_id": base["artifact_id"],
            "base_revision_id": base["revision"]["id"], "draft_sha256": digest(drafts),
            "quality_report_sha256": quality_report["report_sha256"], "decisions": []}


def _check_quality(base: dict[str, Any], drafts: dict[str, Any], quality_report: dict[str, Any]) -> None:
    _check_schema(quality_report, "knowledge-semantic-quality-v1.schema.json")
    # Import at call time because workflow orchestration itself uses V3.
    from .semantic_workflow import guide_hash
    if quality_report["input_hashes"]["guide"] != guide_hash():
        raise KnowledgeFoundationError("V3 quality report guide differs from the installed guide.")
    if (quality_report.get("schema_version") != "knowledge-semantic-quality/v1"
            or quality_report.get("artifact_id") != base["artifact_id"]
            or quality_report.get("base_sha256") != digest(base)
            or quality_report.get("draft_sha256") != digest(drafts)
            or quality_report.get("profile_sha256") != base["profile"]["definition_sha256"]
            or quality_report.get("status") != "pass"
            or quality_report.get("report_sha256") != digest({k: v for k, v in quality_report.items() if k != "report_sha256"})):
        raise KnowledgeFoundationError("V3 publication requires a passing assessment bound to this exact candidate.")


def record_decision(base: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any],
                    source_checks: dict[str, Any], quality_report: dict[str, Any], *,
                    proposal_id: str, decision: str, reviewer: str, checked_anchors: list[str],
                    check_method: str, note: str, root: Path,
                    checked_regions: list[str] | None = None) -> dict[str, Any]:
    validate_drafts(base, drafts, root=root)
    _check_quality(base, drafts, quality_report)
    if any(ledger.get(k) != v for k, v in new_review_ledger(base, drafts, quality_report).items() if k != "decisions"):
        raise KnowledgeFoundationError("V3 review ledger changed candidate or quality input.")
    if any(source_checks.get(k) != v for k, v in new_source_check_ledger(base).items() if k != "checks"):
        raise KnowledgeFoundationError("V3 source verification ledger changed source.")
    proposal = next((p for p in drafts["proposals"] if p["id"] == proposal_id), None)
    if proposal is None or decision not in ("accept", "reject", "pending") or not reviewer.strip():
        raise KnowledgeFoundationError("V3 review needs a proposal, verdict and real reviewer.")
    needed = {anchor_key(a) for a in _proposal_anchors(base, proposal)}
    needed_regions = {region_key(r) for r in _proposal_regions(base, proposal)}
    checked, checked_r = set(checked_anchors), set(checked_regions or [])
    if not checked <= needed or not checked_r <= needed_regions or decision == "accept" and (not needed <= checked or not needed_regions <= checked_r):
        raise KnowledgeFoundationError("V3 semantic review must identify exact affected source evidence.")
    if decision == "accept" and base["source"]["kind"] == "pdf" and check_method != "original_visual":
        raise KnowledgeFoundationError("V3 PDF semantic review must inspect original pages.")
    independent = {key for check in source_checks["checks"] for key in check["anchor_keys"]}
    independent_r = {key for check in source_checks["checks"] for key in check.get("region_keys", [])}
    if decision == "accept" and (not needed <= independent or not needed_regions <= independent_r):
        raise KnowledgeFoundationError("V3 accepted proposal lacks separate source verification.")
    details = {"proposal_id": proposal_id, "base_revision_id": base["revision"]["id"],
               "decision": decision, "reviewer": reviewer.strip(), "checked_anchor_keys": sorted(checked),
               "checked_region_keys": sorted(checked_r), "check_method": check_method, "note": note,
               "proposal_sha256": digest(proposal), "dependency_sha256": _dependency(base, proposal, quality_report),
               "rule_ids": proposal["rule_ids"], "quality_report_sha256": quality_report["report_sha256"]}
    prior = next((x for x in ledger["decisions"] if x["proposal_id"] == proposal_id), None)
    if prior is not None:
        if all(prior.get(key) == value for key, value in details.items()):
            return deepcopy(ledger)
        raise KnowledgeFoundationError("V3 proposal already has a different review decision.")
    later = deepcopy(ledger)
    later["decisions"].append({**details, "reviewed_at": datetime.now(timezone.utc).isoformat()})
    return later


def publish(base: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any],
            source_checks: dict[str, Any], quality_report: dict[str, Any], *,
            revision_id: str, root: Path, dependency_certificate: Any = None) -> dict[str, Any]:
    validate_drafts(base, drafts, root=root)
    _check_quality(base, drafts, quality_report)
    if not revision_id or revision_id == base["revision"]["id"] or any(
            ledger.get(k) != v for k, v in new_review_ledger(base, drafts, quality_report).items() if k != "decisions"):
        raise KnowledgeFoundationError("V3 publication identity mismatch.")
    decisions = {x["proposal_id"]: x for x in ledger["decisions"]}
    if set(decisions) != {x["id"] for x in drafts["proposals"]} or any(x["decision"] != "accept" for x in decisions.values()):
        raise KnowledgeFoundationError("V3 publication requires acceptance of every assessed proposal.")
    later = candidate_preview(base, drafts, root=root)
    later["revision"] = {"id": revision_id, "parent_id": base["revision"]["id"],
                         "note": "Published independently checked v3 semantic revision."}
    checks = {x["id"]: x for x in later["source_checks"]}
    for check in source_checks["checks"]:
        if check["id"] in checks and checks[check["id"]] != check:
            raise KnowledgeFoundationError("V3 source check ID conflicts with a prior event.")
        checks[check["id"]] = deepcopy(check)
    later["source_checks"] = list(checks.values())
    reviewed = set()
    for proposal in drafts["proposals"]:
        decision = decisions[proposal["id"]]
        if (decision["proposal_sha256"] != digest(proposal)
                or decision["dependency_sha256"] != _dependency(base, proposal, quality_report)
                or decision["quality_report_sha256"] != quality_report["report_sha256"]):
            raise KnowledgeFoundationError("V3 proposal/dependency/assessment changed after review.")
        review_id = f"review-{proposal['id']}-{decision['proposal_sha256'][:12]}"
        if any(x["id"] == review_id for x in later["reviews"]):
            raise KnowledgeFoundationError("V3 duplicate review event.")
        later["reviews"].append({"id": review_id, **deepcopy(decision)})
        for kind in ("elements", "relations"):
            for proposed in proposal["upsert_" + kind]:
                item = next(x for x in later[kind] if x["id"] == proposed["id"])
                required = {anchor_key(a) for a in item["anchors"]}
                required_regions = {region_key(r) for r in item.get("regions", [])}
                covering = [x["id"] for x in source_checks["checks"] if required <= set(x["anchor_keys"])
                            and required_regions <= set(x.get("region_keys", []))]
                if not covering:
                    raise KnowledgeFoundationError(f"V3 object lacks one complete independent source check: {item['id']}")
                item.update({"source_check_ids": covering, "review_state": "accepted", "review_id": review_id})
                reviewed.add(item["id"])
    if quality_report.get("reuse_binding"):
        from .semantic_dependency_cert import plan_delta
        if dependency_certificate is None:
            raise KnowledgeFoundationError("V3 partial reuse publication requires its verified certificate proof.")
        plan = plan_delta(base, later, dependency_certificate)
        if quality_report["reuse_binding"] != {"certificate_sha256": plan["certificate_sha256"], "plan_sha256": plan["plan_sha256"]}:
            raise KnowledgeFoundationError("V3 reuse quality does not bind the current publication plan.")
    elif dependency_certificate is not None:
        raise KnowledgeFoundationError("V3 certificate publication requires assessed target reuse provenance.")
    affected, _, _ = _review_scope(base, later, dependency_certificate)
    for item in later["elements"] + later["relations"]:
        if item["id"] not in reviewed and item["id"] in affected:
            raise KnowledgeFoundationError(f"V3 dependency changed without fresh review: {item['id']}")
    validate_projection(later, root=root, resolve=True)
    return later


def _order_key(projection: dict[str, Any], element_id: str) -> tuple[int, int, int]:
    source_order = {u["locator"]: u["order"] for u in projection["source_units"]}
    positions = [(source_order[x["locator"]], x["start"], n)
                 for n, x in enumerate(projection["occurrences"]) if x["element_id"] == element_id]
    return min(positions) if positions else (10**9, 10**9, 10**9)


def query(projection: dict[str, Any], *, term: str = "", package_id: str | None = None,
          role: str | None = None, layer: str = "source") -> dict[str, Any]:
    validate_projection(projection)
    if layer not in ("source", "all", "interpretation"):
        raise KnowledgeFoundationError("V3 query layer must be source, interpretation or all.")
    elements = {x["id"]: x for x in projection["elements"]}
    relations = {x["id"]: x for x in projection["relations"]}
    needle = term.casefold().strip()
    chosen = [p for p in projection["packages"] if (package_id is None or p["id"] == package_id)
              and (role is None or any(elements[i]["kind"] == role for i in p["element_ids"]))
              and (not needle or needle in " ".join([p["summary"], *(elements[i]["text"] for i in p["element_ids"])]).casefold())]
    initial = {ident for p in chosen for ident in p["element_ids"]}
    relevant = {ident for p in chosen for ident in p["relation_ids"]}
    relevant.update(r["id"] for r in relations.values() if r["from_id"] in initial or r["to_id"] in initial)
    def visible(obj: dict[str, Any]) -> bool:
        identity = obj["origin_type"]
        return (layer == "all" or layer == "interpretation" and identity == "project_interpretation"
                or layer == "source" and identity in ("source_claim", "source_example"))
    edge_ids = {ident for ident in relevant if visible(relations[ident])}
    closure = initial | {ident for edge_id in edge_ids for ident in (relations[edge_id]["from_id"], relations[edge_id]["to_id"])}
    if layer != "all":
        closure = {ident for ident in closure if visible(elements[ident])} | {
            ident for edge_id in edge_ids for ident in (relations[edge_id]["from_id"], relations[edge_id]["to_id"])}
    locators = {a["locator"] for ident in closure for a in elements[ident]["anchors"]}
    order = sorted(closure, key=lambda ident: (*_order_key(projection, ident), ident))
    def relation_key(ident: str) -> tuple[Any, ...]:
        edge = relations[ident]
        return (*_order_key(projection, edge["from_id"]), *_order_key(projection, edge["to_id"]), ident)
    return {"schema_version": VERSION, "artifact_id": projection["artifact_id"],
            "revision_id": projection["revision"]["id"], "layer": layer, "profile": deepcopy(projection["profile"]),
            "source": {k: projection["source"][k] for k in ("id", "sha256", "snapshot_sha256")},
            "packages": deepcopy(chosen),
            "package_refs": [{"id": p["id"], "summary": p["summary"]} for p in projection["packages"] if set(p["element_ids"]) & closure],
            "elements": [deepcopy(elements[ident]) for ident in order],
            "relations": [deepcopy(relations[ident]) for ident in sorted(edge_ids, key=relation_key)],
            "occurrences": [deepcopy(o) for o in projection["occurrences"] if o["element_id"] in closure],
            "information": [deepcopy(x) for x in projection["information"] if x["locator"] in locators],
            "discourse": [deepcopy(x) for x in projection["discourse"] if x["locator"] in locators],
            "answer_gap": None if chosen else "No reviewed package in this revision answers the selection; inspect source audit before concluding silence."}


def render_markdown(projection: dict[str, Any], *, layer: str = "source") -> str:
    found = query(projection, layer=layer)
    elements = {x["id"]: x for x in found["elements"]}
    relations = {x["id"]: x for x in found["relations"]}
    lines = [f"# {projection['source']['title']} — 来源语义视图", "",
             f"发布 `{projection['revision']['id']}` · 规范 `{projection['profile']['id']}@{projection['profile']['version']}` · 关系层 `{layer}`", "",
             "来源核验与语义审阅不评估科学有效性或个人适用性。", "", "## 来源顺序", ""]
    for index, ident in enumerate(elements, 1):
        item = elements[ident]
        locations = ", ".join(f"{a['locator']}[{a['start']}:{a['end']}]" for a in item["anchors"])
        lines.append(f"{index}. `{ident}` {item['kind']} · {item['origin_type']} · {item['modality']} · {'否定' if item['negated'] else '非否定'}：{item['text']}（{locations}）")
    lines.extend(["", "## 内容包及关系", ""])
    for package in found["packages"]:
        lines.append(f"### {package['summary']} (`{package['id']}`)")
        for ident in package["element_ids"]:
            if ident in elements:
                lines.append(f"- 要素 `{ident}`：{elements[ident]['text']}")
        for ident in package["relation_ids"]:
            if ident in relations:
                edge = relations[ident]
                connector = "↔" if edge["type"] == "contrasts_with" else "→"
                lines.append(f"- 关系 `{ident}`：`{edge['from_id']}` {connector} `{edge['to_id']}` · {edge['type']} · {edge['origin_type']} / {edge['basis']}；{edge['rationale'] or edge['note']}")
        lines.append("")
    lines.extend(["## 关系图", "", "```mermaid", "flowchart LR"])
    alias = {ident: f"n{index}" for index, ident in enumerate(elements)}
    for ident, item in elements.items():
        lines.append(f"    {alias[ident]}[\"{item['kind']}: {ident}\"]")
    for edge in relations.values():
        shape = " --- " if edge["type"] == "contrasts_with" else " --> "
        lines.append(f"    {alias[edge['from_id']]}{shape}{alias[edge['to_id']]}")
    lines.extend(["```", "", "## 信息与论述缺口", ""])
    for item in found["information"] + found["discourse"]:
        if item["status"] != "modeled":
            lines.append(f"- `{item['id']}` {item['status']}：{item['meaning']}；{item['reason']}")
    return "\n".join(lines).rstrip() + "\n"


def export(projection: dict[str, Any], *, root: Path, producer_commit: str) -> dict[str, Any]:
    validate_projection(projection, root=root, resolve=True)
    if not producer_commit.strip():
        raise KnowledgeFoundationError("V3 export requires a producer commit or release.")
    return {"schema_version": EXPORT_VERSION,
            "producer": {"name": "source-knowledge-modeling", "commit_or_release": producer_commit.strip()},
            "artifact_schema_version": VERSION, "artifact_id": projection["artifact_id"],
            "artifact_revision_id": projection["revision"]["id"],
            **{key: deepcopy(projection[key]) for key in ("profile", "source", "scope", "source_units", "occurrences",
                                                       "screening", "information", "discourse", "elements", "relations",
                                                       "packages", "source_checks", "reviews", "lineage")}}


def impact(old: dict[str, Any], new: dict[str, Any], *, dependency_certificate: Any = None) -> dict[str, Any]:
    validate_projection(old)
    validate_projection(new)
    if old["artifact_id"] != new["artifact_id"] or new["revision"]["parent_id"] != old["revision"]["id"]:
        raise KnowledgeFoundationError("V3 impact needs consecutive revisions of one artifact.")
    def changed(key: str, revision: bool = False) -> list[str]:
        before = {x["id"]: x["content_revision"] if revision else x for x in old[key]}
        after = {x["id"]: x["content_revision"] if revision else x for x in new[key]}
        return sorted(ident for ident in before.keys() | after.keys() if before.get(ident) != after.get(ident))
    objects = changed("elements", True) + changed("relations", True)
    # A scope/source-unit change can alter coverage or implicit context even when
    # every existing object's text and citations remain identical.
    affected, dependent, reasons = _review_scope(old, new, dependency_certificate)
    order_changed = old["source_units"] != new["source_units"] or old["occurrences"] != new["occurrences"]
    return {"from_revision": old["revision"]["id"], "to_revision": new["revision"]["id"],
            "source_changed": old["source"] != new["source"], "profile_changed": old["profile"] != new["profile"],
            "scope_changed": old["scope"] != new["scope"],
            "conservative_recheck_reasons": reasons,
            "changed_element_ids": changed("elements", True), "changed_relation_ids": changed("relations", True),
            "changed_package_ids": changed("packages"), "changed_information_ids": changed("information"),
            "changed_discourse_ids": changed("discourse"), "source_order_changed": order_changed,
            "dependent_object_ids": sorted(dependent),
            "recheck_object_ids": sorted(affected),
            "rebuild_views": bool(reasons or order_changed),
            "rebuild_export": bool(reasons or order_changed)}
