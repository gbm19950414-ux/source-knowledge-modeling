"""Versioned, source-linked semantic projections beside readable foundation v1 artifacts."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .core import KnowledgeFoundationError, digest_bytes, load_json


VERSION = "knowledge-semantic-projection/v1"
DRAFT_VERSION = "knowledge-semantic-drafts/v1"
REVIEW_VERSION = "knowledge-semantic-review/v1"
EXPORT_VERSION = "source-knowledge-export/v2"
PROFILE_ID = "instructional-source"
PROFILE_VERSION = "1.0.0"


def _schema() -> dict[str, Any]:
    return load_json(Path(__file__).resolve().parent / "schemas/knowledge-semantic-projection.schema.json")


def profile_manifest(profile_id: str = PROFILE_ID, version: str = PROFILE_VERSION) -> dict[str, str]:
    base = Path(__file__).resolve().parent / "profiles" / f"{profile_id}-{version}"
    config_path, guide_path = Path(str(base) + ".json"), Path(str(base) + ".md")
    try:
        config_bytes, guide_bytes = config_path.read_bytes(), guide_path.read_bytes()
    except OSError as exc:
        raise KnowledgeFoundationError(f"Unknown semantic profile: {profile_id}@{version}") from exc
    config = json.loads(config_bytes)
    if config.get("id") != profile_id or config.get("version") != version:
        raise KnowledgeFoundationError("Semantic profile file identity mismatch.")
    return {"id": profile_id, "version": version, "definition_sha256": digest_bytes(config_bytes + b"\n---guide---\n" + guide_bytes)}


def anchor_key(anchor: dict[str, Any]) -> str:
    return digest_bytes(json.dumps(anchor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def proposal_hash(proposal: dict[str, Any]) -> str:
    return digest_bytes(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def all_anchors(statement: dict[str, Any]) -> list[dict[str, Any]]:
    return [anchor for part in statement["elements"] + statement["relations"] for anchor in part["anchors"]]


def _snapshot_texts(projection: dict[str, Any], root: Path) -> dict[str, str]:
    source = projection["source"]
    paths = ((source["path"], source["sha256"]), (source["snapshot_path"], source["snapshot_sha256"]))
    for relative, expected in paths:
        path = root / relative
        try:
            actual = digest_bytes(path.read_bytes())
        except OSError as exc:
            raise KnowledgeFoundationError(f"Source file unavailable: {path}") from exc
        if actual != expected:
            raise KnowledgeFoundationError(f"Source revision mismatch: {path}")
    snapshot = load_json(root / source["snapshot_path"])
    units = snapshot.get("units")
    if not isinstance(units, list):
        raise KnowledgeFoundationError("Semantic projection needs a unit transcription snapshot.")
    return {unit["locator"]: unit["text"] for unit in units}


def _check_anchors(statement: dict[str, Any], texts: dict[str, str] | None, locators: set[str]) -> None:
    for anchor in all_anchors(statement):
        if anchor["locator"] not in locators:
            raise KnowledgeFoundationError(f"Anchor outside screened source: {anchor['locator']}")
        if anchor["end"] <= anchor["start"]:
            raise KnowledgeFoundationError("Source anchor has an empty or reversed span.")
        if texts is not None and texts.get(anchor["locator"], "")[anchor["start"]:anchor["end"]] != anchor["quote"]:
            raise KnowledgeFoundationError(f"Source anchor quote mismatch: {anchor['locator']}")


def _check_statement(statement: dict[str, Any], *, published: bool, source_kind: str, texts: dict[str, str] | None, locators: set[str]) -> None:
    schema = _schema()
    if not published:
        schema["$defs"]["statement"]["required"].remove("review")
    errors = list(Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/statement"}).iter_errors(statement))
    if errors:
        error = errors[0]
        raise KnowledgeFoundationError(f"Semantic statement schema error at {list(error.path)}: {error.message}")
    elements = {element["id"]: element for element in statement["elements"]}
    if len(elements) != len(statement["elements"]):
        raise KnowledgeFoundationError(f"Duplicate semantic element ID in {statement['id']}")
    relation_ids = [relation["id"] for relation in statement["relations"]]
    if len(relation_ids) != len(set(relation_ids)):
        raise KnowledgeFoundationError(f"Duplicate semantic relation ID in {statement['id']}")
    for relation in statement["relations"]:
        if relation["from_id"] not in elements or relation["to_id"] not in elements or relation["from_id"] == relation["to_id"]:
            raise KnowledgeFoundationError(f"Semantic relation has invalid endpoints: {relation['id']}")
        expected = {"conditions": "condition", "exception_to": "exception", "replaces": "action", "supports": "reason", "precedes": "action", "quantifies": "parameter", "example_of": "example"}.get(relation["type"])
        if expected and elements[relation["from_id"]]["kind"] != expected:
            raise KnowledgeFoundationError(f"Semantic relation direction/kind mismatch: {relation['id']}")
        if relation["type"] == "precedes" and elements[relation["to_id"]]["kind"] != "action":
            raise KnowledgeFoundationError(f"Precedes must connect ordered actions: {relation['id']}")
        if relation["type"] == "replaces" and elements[relation["to_id"]]["kind"] != "action":
            raise KnowledgeFoundationError(f"Replaces must target the default action: {relation['id']}")
        if relation["type"] == "conditions" and elements[relation["to_id"]]["kind"] != "action":
            raise KnowledgeFoundationError(f"Condition must target an action: {relation['id']}")
        if relation["type"] == "exception_to" and elements[relation["to_id"]]["kind"] != "action":
            raise KnowledgeFoundationError(f"Exception must target the default action: {relation['id']}")
        if relation["type"] == "quantifies" and elements[relation["to_id"]]["kind"] not in ("action", "assertion", "example", "exception"):
            raise KnowledgeFoundationError(f"Parameter must target an action, assertion, example or exception: {relation['id']}")
        if relation["type"] == "supports" and elements[relation["to_id"]]["kind"] not in ("action", "assertion"):
            raise KnowledgeFoundationError(f"Reason must support an action or assertion: {relation['id']}")
    successors: dict[str, list[str]] = {}
    for relation in statement["relations"]:
        if relation["type"] == "precedes":
            successors.setdefault(relation["from_id"], []).append(relation["to_id"])
    visited: set[str] = set()
    active: set[str] = set()
    def walk(node: str) -> None:
        if node in active:
            raise KnowledgeFoundationError(f"Cyclic step order in {statement['id']}")
        if node in visited:
            return
        active.add(node)
        for later in successors.get(node, []):
            walk(later)
        active.remove(node)
        visited.add(node)
    for start in successors:
        walk(start)
    for element in statement["elements"]:
        if (element["kind"] == "parameter") != ("quantity" in element):
            raise KnowledgeFoundationError(f"Parameter must have amount, unit and basis: {element['id']}")
        if element["kind"] == "parameter" and not any(r["type"] == "quantifies" and r["from_id"] == element["id"] for r in statement["relations"]):
            raise KnowledgeFoundationError(f"Parameter has no target object: {element['id']}")
    _check_anchors(statement, texts, locators)
    if published:
        checked = set(statement["review"]["checked_anchors"])
        if not {anchor_key(anchor) for anchor in all_anchors(statement)} <= checked:
            raise KnowledgeFoundationError(f"Review does not cover every semantic span: {statement['id']}")
        if source_kind == "pdf" and statement["origin_type"] in ("source_claim", "source_example") and statement["review"]["check_method"] != "original_visual":
            raise KnowledgeFoundationError(f"PDF source statement needs original visual review: {statement['id']}")


def new_projection(foundation: dict[str, Any], legacy_bytes: bytes, screening: list[dict[str, str]]) -> dict[str, Any]:
    from .core import validate
    validate(foundation)
    return {
        "schema_version": VERSION,
        "artifact_id": foundation["artifact_id"] + ".semantic",
        "revision": {"id": "semantic-r0", "parent_id": None, "note": "Screened legacy source; no new semantic statement published."},
        "profile": profile_manifest(), "source": deepcopy(foundation["source"]),
        "legacy": {"artifact_id": foundation["artifact_id"], "revision_id": foundation["revision"]["id"], "sha256": digest_bytes(legacy_bytes), "profile_status": "legacy/unversioned"},
        "scope": {**deepcopy(foundation["scope"]), "included_locators": [u["locator"] for u in foundation["units"]]},
        "screening": deepcopy(screening), "statements": [],
        "lineage": [{"legacy_kind": kind, "legacy_id": item["id"], "status": "deferred", "new_ids": []}
                    for kind, key in (("annotation", "annotations"), ("relation", "relations")) for item in foundation[key]],
        "supersessions": [],
    }


def validate_projection(projection: dict[str, Any], *, root: Path | None = None, resolve: bool = False) -> dict[str, Any]:
    errors = list(Draft202012Validator(_schema()).iter_errors(projection))
    if errors:
        error = errors[0]
        raise KnowledgeFoundationError(f"Semantic projection schema error at {list(error.path)}: {error.message}")
    if projection["profile"] != profile_manifest(projection["profile"]["id"], projection["profile"]["version"]):
        raise KnowledgeFoundationError("Semantic profile definition hash mismatch.")
    if resolve and root is None:
        raise KnowledgeFoundationError("Semantic source resolution needs a root.")
    texts = _snapshot_texts(projection, root) if resolve and root else None
    screened = projection["screening"]
    locators = [item["locator"] for item in screened]
    if len(locators) != len(set(locators)) or set(locators) != set(projection["scope"]["included_locators"]):
        raise KnowledgeFoundationError("Screening must cover each included source locator exactly once.")
    if texts is not None and set(texts) != set(locators):
        raise KnowledgeFoundationError("Screening differs from frozen source snapshot.")
    statement_ids = [statement["id"] for statement in projection["statements"]]
    if len(statement_ids) != len(set(statement_ids)):
        raise KnowledgeFoundationError("Duplicate semantic statement ID.")
    for statement in projection["statements"]:
        _check_statement(statement, published=True, source_kind=projection["source"]["kind"], texts=texts, locators=set(locators))
    lineage_keys = [(link["legacy_kind"], link["legacy_id"]) for link in projection["lineage"]]
    if len(lineage_keys) != len(set(lineage_keys)):
        raise KnowledgeFoundationError("Duplicate legacy mapping.")
    for link in projection["lineage"]:
        if not set(link["new_ids"]) <= set(statement_ids) or (link["status"] == "deferred") != (not link["new_ids"]):
            raise KnowledgeFoundationError(f"Invalid legacy mapping: {link['legacy_id']}")
    return {"revision_id": projection["revision"]["id"], "unit_count": len(screened), "statement_count": len(statement_ids),
            "screening": dict(Counter(item["status"] for item in screened))}


def validate_semantic_drafts(projection: dict[str, Any], drafts: dict[str, Any], *, root: Path) -> None:
    validate_projection(projection, root=root, resolve=True)
    if drafts.get("schema_version") != DRAFT_VERSION or drafts.get("artifact_id") != projection["artifact_id"] or drafts.get("base_revision_id") != projection["revision"]["id"]:
        raise KnowledgeFoundationError("Semantic draft identity or base revision mismatch.")
    proposals = drafts.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise KnowledgeFoundationError("Semantic drafts need nonempty proposals.")
    proposal_ids = [item.get("id") for item in proposals if isinstance(item, dict)]
    if len(proposal_ids) != len(proposals) or any(not isinstance(i, str) or not i for i in proposal_ids) or len(set(proposal_ids)) != len(proposal_ids):
        raise KnowledgeFoundationError("Semantic proposal IDs must be unique.")
    existing = {item["id"] for item in projection["statements"]}
    retired: set[str] = set()
    added: set[str] = set()
    texts = _snapshot_texts(projection, root)
    locators = {item["locator"] for item in projection["screening"]}
    for proposal in proposals:
        values, retire_ids = proposal.get("values"), proposal.get("retire_ids")
        if not isinstance(values, list) or not isinstance(retire_ids, list) or not values and not retire_ids:
            raise KnowledgeFoundationError(f"Semantic proposal needs values or retired IDs: {proposal['id']}")
        if not all(isinstance(i, str) and i in existing for i in retire_ids) or len(retire_ids) != len(set(retire_ids)) or retired.intersection(retire_ids):
            raise KnowledgeFoundationError(f"Invalid semantic retirement: {proposal['id']}")
        retired.update(retire_ids)
        for statement in values:
            if not isinstance(statement, dict) or "review" in statement:
                raise KnowledgeFoundationError(f"Draft cannot carry published review: {proposal['id']}")
            _check_statement(statement, published=False, source_kind=projection["source"]["kind"], texts=texts, locators=locators)
            if statement["id"] in added or statement["id"] in existing and statement["id"] not in retire_ids:
                raise KnowledgeFoundationError(f"Semantic draft would overwrite a published statement: {statement['id']}")
            added.add(statement["id"])


def new_semantic_ledger(drafts: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": REVIEW_VERSION, "artifact_id": drafts["artifact_id"], "base_revision_id": drafts["base_revision_id"], "decisions": []}


def _proposal_anchors(projection: dict[str, Any], proposal: dict[str, Any]) -> list[dict[str, Any]]:
    old = {statement["id"]: statement for statement in projection["statements"]}
    return [anchor for statement in proposal["values"] + [old[i] for i in proposal["retire_ids"]] for anchor in all_anchors(statement)]


def record_semantic_decision(projection: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], *, proposal_id: str, decision: str, reviewer: str, checked_anchors: list[str], check_method: str, note: str, root: Path) -> dict[str, Any]:
    validate_semantic_drafts(projection, drafts, root=root)
    if ledger.get("schema_version") != REVIEW_VERSION or ledger.get("artifact_id") != drafts["artifact_id"] or ledger.get("base_revision_id") != drafts["base_revision_id"]:
        raise KnowledgeFoundationError("Semantic review ledger identity mismatch.")
    proposal = next((item for item in drafts["proposals"] if item["id"] == proposal_id), None)
    if proposal is None or decision not in ("accept", "reject", "pending") or not reviewer.strip():
        raise KnowledgeFoundationError("Semantic review needs proposal, decision and reviewer.")
    if check_method not in ("original_visual", "source_text", "parsed_text"):
        raise KnowledgeFoundationError("Invalid semantic check method.")
    required = {anchor_key(anchor) for anchor in _proposal_anchors(projection, proposal)}
    if decision == "accept" and (check_method != "original_visual" if projection["source"]["kind"] == "pdf" else check_method == "parsed_text"):
        raise KnowledgeFoundationError("Acceptance requires an original source check.")
    if decision == "accept" and not required <= set(checked_anchors):
        raise KnowledgeFoundationError("Accepted semantic proposal lacks checked source spans.")
    if not set(checked_anchors) <= required:
        raise KnowledgeFoundationError("Semantic review cites an unrelated source span.")
    later = deepcopy(ledger)
    later["decisions"] = [item for item in later["decisions"] if item.get("proposal_id") != proposal_id]
    later["decisions"].append({"proposal_id": proposal_id, "decision": decision, "reviewer": reviewer.strip(),
                               "checked_anchors": sorted(set(checked_anchors)), "check_method": check_method,
                               "note": note, "proposal_sha256": proposal_hash(proposal), "reviewed_at": datetime.now(timezone.utc).isoformat()})
    return later


def publish_semantic(projection: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], *, revision_id: str, root: Path) -> dict[str, Any]:
    validate_semantic_drafts(projection, drafts, root=root)
    if ledger.get("schema_version") != REVIEW_VERSION or ledger.get("artifact_id") != drafts["artifact_id"] or ledger.get("base_revision_id") != drafts["base_revision_id"]:
        raise KnowledgeFoundationError("Semantic review ledger identity mismatch.")
    if not revision_id or revision_id == projection["revision"]["id"]:
        raise KnowledgeFoundationError("Semantic publication needs a new revision ID.")
    decisions = ledger.get("decisions")
    if not isinstance(decisions, list) or len({item.get("proposal_id") for item in decisions if isinstance(item, dict)}) != len(decisions):
        raise KnowledgeFoundationError("Duplicate or invalid semantic decisions.")
    accepted = {item["proposal_id"]: item for item in decisions if item.get("decision") == "accept"}
    if not accepted:
        raise KnowledgeFoundationError("No accepted semantic proposal.")
    later = deepcopy(projection)
    later["revision"] = {"id": revision_id, "parent_id": projection["revision"]["id"], "note": "Published source-checked semantic changes."}
    by_id = {item["id"]: item for item in later["statements"]}
    for proposal in drafts["proposals"]:
        decision = accepted.get(proposal["id"])
        if not decision:
            continue
        if decision.get("proposal_sha256") != proposal_hash(proposal):
            raise KnowledgeFoundationError(f"Accepted semantic draft changed since review: {proposal['id']}")
        required = {anchor_key(anchor) for anchor in _proposal_anchors(projection, proposal)}
        if not required <= set(decision.get("checked_anchors", [])) or projection["source"]["kind"] == "pdf" and decision.get("check_method") != "original_visual":
            raise KnowledgeFoundationError(f"Accepted semantic draft lacks original source review: {proposal['id']}")
        for old_id in proposal["retire_ids"]:
            del by_id[old_id]
        new_ids = []
        for value in proposal["values"]:
            item = deepcopy(value)
            item["review"] = {key: decision[key] for key in ("decision", "reviewer", "check_method", "checked_anchors", "reviewed_at", "proposal_sha256", "note")}
            by_id[item["id"]] = item
            new_ids.append(item["id"])
        if proposal["retire_ids"]:
            later["supersessions"].append({"from_revision": projection["revision"]["id"], "old_ids": proposal["retire_ids"], "new_ids": new_ids})
    later["statements"] = list(by_id.values())
    anchored_locators = {anchor["locator"] for statement in later["statements"] for anchor in all_anchors(statement)}
    for item in later["screening"]:
        if item["locator"] in anchored_locators and item["status"] in ("unmodeled", "partial"):
            item["status"] = "partial"
            item["reason"] = "Specific spans are published; remaining content in this source unit has not been certified as fully modeled."
    for link in later["lineage"]:
        new_ids = [statement["id"] for statement in later["statements"] if link["legacy_kind"] == "annotation" and link["legacy_id"] in statement["legacy_ids"]]
        link["new_ids"] = new_ids
        # A shared legacy ID proves provenance, not full semantic equivalence.
        link["status"] = "partial" if new_ids else "deferred"
    validate_projection(later, root=root, resolve=True)
    return later


def query_projection(projection: dict[str, Any], *, term: str = "", statement_id: str | None = None, role: str | None = None, include_all_gaps: bool = False) -> dict[str, Any]:
    needle = term.casefold().strip()
    chosen = [statement for statement in projection["statements"] if
              (statement_id is None or statement["id"] == statement_id) and
              (role is None or any(element["kind"] == role for element in statement["elements"])) and
              (not needle or needle in " ".join([statement["summary"], *[element["text"] for element in statement["elements"]], *[relation["note"] for relation in statement["relations"]]]).casefold())]
    relevant_locators = {anchor["locator"] for statement in chosen for anchor in all_anchors(statement)}
    gaps = [item for item in projection["screening"] if item["status"] in ("partial", "unmodeled", "uncertain")]
    return {"schema_version": VERSION, "artifact_id": projection["artifact_id"], "revision_id": projection["revision"]["id"],
            "profile": projection["profile"], "source": {"id": projection["source"]["id"], "sha256": projection["source"]["sha256"], "snapshot_sha256": projection["source"]["snapshot_sha256"]},
            "statements": deepcopy(chosen),
            "answer_gap": "No reviewed statement in this semantic revision answers the selection; inspect source and screening before concluding the book is silent." if not chosen and (needle or statement_id or role) else None,
            "gap_summary": dict(Counter(item["status"] for item in gaps)),
            "gaps": gaps if include_all_gaps else [item for item in gaps if item["locator"] in relevant_locators]}


def render_projection(projection: dict[str, Any]) -> str:
    found = query_projection(projection, include_all_gaps=True)
    lines = [f"# {projection['source']['title']} — 语义学习视图", "",
             f"发布版 `{found['revision_id']}` · 规范 `{found['profile']['id']}@{found['profile']['version']}` · 来源 SHA-256 `{found['source']['sha256']}`", "",
             "本视图记录作者表述与来源结构。科学有效性和个人适用性未由来源核对判定。", ""]
    for statement in found["statements"]:
        lines += [f"## {statement['summary']}  (`{statement['id']}`)", "",
                  f"归属：`{statement['origin_type']}`；语气：`{statement['modality']}`；来源审阅：{statement['review']['reviewer']} / {statement['review']['check_method']}；科学：`{statement['scientific_status']}`；适用性：`{statement['applicability_status']}`。", ""]
        for element in statement["elements"]:
            anchored = ", ".join(f"`{a['locator']}[{a['start']}:{a['end']}]`" for a in element["anchors"])
            quantity = element.get("quantity")
            label = f"{element['kind']} `{element['id']}`：{element['text']}"
            if quantity:
                label += f"（{quantity['amount']} {quantity['unit']}；基准：{quantity['basis']}）"
            lines.append(f"- {label}。依据：{anchored}。")
        if statement["relations"]:
            lines += ["", "关系："]
            for relation in statement["relations"]:
                anchored = ", ".join(f"`{a['locator']}[{a['start']}:{a['end']}]`" for a in relation["anchors"])
                lines.append(f"- `{relation['id']}`：`{relation['from_id']}` —{relation['type']}→ `{relation['to_id']}`；依据：{anchored}。{relation['note']}")
        lines.append("")
    lines += ["## 关系图", "", "```mermaid", "flowchart LR"]
    count = 0
    for statement in found["statements"]:
        ids = {}
        for element in statement["elements"]:
            name = f"n{count}"; count += 1; ids[element["id"]] = name
            lines.append(f"    {name}[\"{element['kind']}: {element['id']}\"]")
        for relation in statement["relations"]:
            lines.append(f"    {ids[relation['from_id']]} -->|{relation['type']}| {ids[relation['to_id']]}")
    lines += ["```", "", "## 未完成的来源范围", ""]
    for gap in found["gaps"]:
        lines.append(f"- `{gap['locator']}`：{gap['status']}；{gap['reason']}")
    return "\n".join(lines).rstrip() + "\n"


def export_projection(projection: dict[str, Any], *, root: Path, producer_commit: str) -> dict[str, Any]:
    validate_projection(projection, root=root, resolve=True)
    if not producer_commit.strip():
        raise KnowledgeFoundationError("A producing commit or release is required.")
    return {"schema_version": EXPORT_VERSION, "producer": {"name": "source-knowledge-modeling", "commit_or_release": producer_commit.strip()},
            "artifact_id": projection["artifact_id"], "artifact_revision_id": projection["revision"]["id"],
            "artifact_schema_version": VERSION, "profile": deepcopy(projection["profile"]), "source": deepcopy(projection["source"]),
            "statements": deepcopy(projection["statements"]), "screening": deepcopy(projection["screening"]),
            "lineage": deepcopy(projection["lineage"]), "supersessions": deepcopy(projection["supersessions"])}


def impact_projection(old: dict[str, Any], new: dict[str, Any], *, foundation: dict[str, Any]) -> dict[str, Any]:
    """A conservative impact plan; profile changes rescreen even unannotated source."""
    if new["legacy"]["artifact_id"] != foundation["artifact_id"]:
        raise KnowledgeFoundationError("Legacy foundation identity mismatch.")
    old_is_legacy = old.get("schema_version") == "knowledge-foundation/v1"
    if old_is_legacy:
        if old["artifact_id"] != foundation["artifact_id"] or new["legacy"]["revision_id"] != old["revision"]["id"]:
            raise KnowledgeFoundationError("Legacy revision mismatch.")
        profile_changed = True
        old_statements: dict[str, Any] = {}
    else:
        if old["artifact_id"] != new["artifact_id"] or new["revision"]["parent_id"] != old["revision"]["id"]:
            raise KnowledgeFoundationError("Semantic revision lineage mismatch.")
        profile_changed = old["profile"] != new["profile"]
        old_statements = {s["id"]: s for s in old["statements"]}
    current = {s["id"]: s for s in new["statements"]}
    changed = sorted(i for i in old_statements.keys() | current.keys() if old_statements.get(i) != current.get(i))
    rescreen = [u["locator"] for u in foundation["units"]] if profile_changed else []
    return {"from_revision": old["revision"]["id"], "to_revision": new["revision"]["id"],
            "profile_changed": profile_changed, "source_reused": old["source"]["sha256"] == new["source"]["sha256"] and old["source"]["snapshot_sha256"] == new["source"]["snapshot_sha256"],
            "changed_statement_ids": changed, "review_required_statement_ids": sorted(set(changed) | ({item["id"] for item in new["statements"]} if profile_changed else set())),
            "rescreen_locators": rescreen, "legacy_deferred_ids": [x["legacy_id"] for x in new["lineage"] if x["status"] == "deferred"],
            "affected_views": ["query", "learning_markdown", "relationship_graph", "readonly_export"] if profile_changed or changed else []}
