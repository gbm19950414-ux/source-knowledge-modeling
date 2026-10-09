"""Import external semantic drafts, record source review, publish a new v1 revision."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError, digest_bytes, validate


DRAFT_VERSION = "knowledge-drafts/v1"
REVIEW_VERSION = "knowledge-review/v2"
PUBLISHED_REVIEW_FIELDS = ("decision", "reviewer", "checked_locators", "check_method", "reviewed_at", "proposal_sha256")


def _proposal_sha256(proposal: dict[str, Any]) -> str:
    return digest_bytes(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def validate_drafts(artifact: dict[str, Any], drafts: dict[str, Any]) -> None:
    if drafts.get("schema_version") != DRAFT_VERSION or drafts.get("artifact_id") != artifact.get("artifact_id") or drafts.get("base_revision_id") != artifact["revision"]["id"]:
        raise KnowledgeFoundationError("Draft identity or base revision mismatch.")
    proposals = drafts.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise KnowledgeFoundationError("Drafts need a nonempty proposals list.")
    ids = [proposal.get("id") for proposal in proposals if isinstance(proposal, dict)]
    if len(ids) != len(proposals) or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise KnowledgeFoundationError("Draft proposal IDs must be unique nonempty strings.")
    unit_ids = {unit["id"] for unit in artifact["units"]}
    annotation_ids = {annotation["id"] for annotation in artifact["annotations"]}
    candidate_annotations = {proposal["value"].get("id") for proposal in proposals if proposal.get("kind") == "annotation" and isinstance(proposal.get("value"), dict)}
    for proposal in proposals:
        kind, value = proposal.get("kind"), proposal.get("value")
        if kind not in ("annotation", "relation") or not isinstance(value, dict):
            raise KnowledgeFoundationError(f"Invalid draft kind or value: {proposal['id']}")
        if not isinstance(value.get("id"), str) or not value["id"]:
            raise KnowledgeFoundationError(f"Draft value needs a stable ID: {proposal['id']}")
        if kind == "annotation":
            references = value.get("unit_ids")
            if not isinstance(references, list) or not references or not set(references) <= unit_ids:
                raise KnowledgeFoundationError(f"Draft annotation has invalid source units: {proposal['id']}")
            if value["id"] in annotation_ids:
                raise KnowledgeFoundationError(f"Draft would overwrite a published annotation: {value['id']}")
        else:
            references = value.get("support_unit_ids")
            if not isinstance(references, list) or not references or not set(references) <= unit_ids:
                raise KnowledgeFoundationError(f"Draft relation has invalid source units: {proposal['id']}")
            if value.get("from_id") not in annotation_ids | candidate_annotations or value.get("to_id") not in annotation_ids | candidate_annotations:
                raise KnowledgeFoundationError(f"Draft relation has an unknown endpoint: {proposal['id']}")


def new_review_ledger(drafts: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": REVIEW_VERSION, "artifact_id": drafts["artifact_id"], "base_revision_id": drafts["base_revision_id"], "decisions": []}


def record_decision(
    artifact: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], *,
    proposal_id: str, decision: str, reviewer: str, checked_locators: list[str], note: str,
    check_method: str = "source_text",
) -> dict[str, Any]:
    validate_drafts(artifact, drafts)
    if ledger.get("schema_version") != REVIEW_VERSION or ledger.get("artifact_id") != drafts["artifact_id"] or ledger.get("base_revision_id") != drafts["base_revision_id"]:
        raise KnowledgeFoundationError("Review ledger identity or base revision mismatch.")
    if not isinstance(ledger.get("decisions"), list):
        raise KnowledgeFoundationError("Review ledger decisions must be a list.")
    proposal = next((item for item in drafts["proposals"] if item["id"] == proposal_id), None)
    if proposal is None or decision not in ("accept", "reject", "pending") or not reviewer.strip():
        raise KnowledgeFoundationError("Review needs a known proposal, valid decision, and reviewer.")
    if check_method not in ("original_visual", "source_text", "parsed_text"):
        raise KnowledgeFoundationError("Review needs a valid source check method.")
    if decision == "accept" and artifact["source"]["kind"] == "pdf" and check_method != "original_visual":
        raise KnowledgeFoundationError("PDF acceptance requires an original visual page check.")
    valid_locators = {unit["locator"] for unit in artifact["units"]}
    if not set(checked_locators) <= valid_locators:
        raise KnowledgeFoundationError("Review references a locator absent from the source inventory.")
    required_ids = proposal["value"]["unit_ids" if proposal["kind"] == "annotation" else "support_unit_ids"]
    by_id = {unit["id"]: unit["locator"] for unit in artifact["units"]}
    if decision == "accept" and not {by_id[unit_id] for unit_id in required_ids} <= set(checked_locators):
        raise KnowledgeFoundationError("Accepted proposal must cite every source locator checked by the reviewer.")
    later = deepcopy(ledger)
    later["decisions"] = [item for item in later["decisions"] if item.get("proposal_id") != proposal_id]
    later["decisions"].append({
        "proposal_id": proposal_id, "decision": decision, "reviewer": reviewer.strip(),
        "checked_locators": list(dict.fromkeys(checked_locators)), "note": note,
        "check_method": check_method,
        "proposal_sha256": _proposal_sha256(proposal),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    return later


def publish(artifact: dict[str, Any], drafts: dict[str, Any], ledger: dict[str, Any], *, revision_id: str, root: Path) -> dict[str, Any]:
    validate(artifact, root=root, resolve=True)
    validate_drafts(artifact, drafts)
    if ledger.get("schema_version") != REVIEW_VERSION or ledger.get("artifact_id") != drafts["artifact_id"] or ledger.get("base_revision_id") != drafts["base_revision_id"]:
        raise KnowledgeFoundationError("Review ledger identity or base revision mismatch.")
    if not revision_id or revision_id == artifact["revision"]["id"]:
        raise KnowledgeFoundationError("Publication needs a new revision ID.")
    decisions = ledger.get("decisions")
    if not isinstance(decisions, list) or len({item.get("proposal_id") for item in decisions if isinstance(item, dict)}) != len(decisions):
        raise KnowledgeFoundationError("Review ledger contains duplicate or invalid decisions.")
    accepted = {item["proposal_id"]: item for item in decisions if item.get("decision") == "accept"}
    if not accepted:
        raise KnowledgeFoundationError("No reviewed proposal was accepted.")
    known_locators = {unit["locator"] for unit in artifact["units"]}
    by_id = {unit["id"]: unit for unit in artifact["units"]}
    published = deepcopy(artifact)
    published["revision"] = {"id": revision_id, "parent_id": artifact["revision"]["id"], "note": "Published source-checked draft decisions; see separate review ledger."}
    accepted_annotations: set[str] = set()
    for proposal in drafts["proposals"]:
        if proposal["kind"] != "annotation" or proposal["id"] not in accepted:
            continue
        review = accepted[proposal["id"]]
        if review.get("proposal_sha256") != _proposal_sha256(proposal):
            raise KnowledgeFoundationError(f"Accepted draft changed since review: {proposal['id']}")
        if artifact["source"]["kind"] == "pdf" and review.get("check_method") != "original_visual":
            raise KnowledgeFoundationError(f"Accepted PDF draft lacks an original visual page check: {proposal['id']}")
        refs = proposal["value"]["unit_ids"]
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"] or not set(review.get("checked_locators", [])) <= known_locators or not {by_id[unit_id]["locator"] for unit_id in refs} <= set(review["checked_locators"]):
            raise KnowledgeFoundationError(f"Accepted draft lacks complete source review: {proposal['id']}")
        value = deepcopy(proposal["value"])
        value["source_checked"] = True
        value["review"] = {name: deepcopy(review[name]) for name in PUBLISHED_REVIEW_FIELDS}
        published["annotations"].append(value)
        accepted_annotations.add(value["id"])
        for unit_id in refs:
            unit = next(unit for unit in published["units"] if unit["id"] == unit_id)
            if unit["interpretation_status"] == "unreviewed":
                unit["interpretation_status"] = "partially_interpreted"
                unit["review_note"] = "A reviewed claim exists; other source details may remain uninterpreted."
            if unit["transcription_status"] == "machine_extracted":
                unit["transcription_status"] = "spot_checked"
                unit["review_note"] += " The cited wording was checked against the original."
    known_annotations = {item["id"] for item in published["annotations"]}
    for proposal in drafts["proposals"]:
        if proposal["kind"] != "relation" or proposal["id"] not in accepted:
            continue
        review = accepted[proposal["id"]]
        if review.get("proposal_sha256") != _proposal_sha256(proposal):
            raise KnowledgeFoundationError(f"Accepted draft changed since review: {proposal['id']}")
        if artifact["source"]["kind"] == "pdf" and review.get("check_method") != "original_visual":
            raise KnowledgeFoundationError(f"Accepted PDF relation lacks an original visual page check: {proposal['id']}")
        value = deepcopy(proposal["value"])
        refs = value["support_unit_ids"]
        if value["from_id"] not in known_annotations or value["to_id"] not in known_annotations:
            raise KnowledgeFoundationError(f"Accepted relation depends on an unpublished annotation: {proposal['id']}")
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"] or not {by_id[unit_id]["locator"] for unit_id in refs} <= set(review.get("checked_locators", [])):
            raise KnowledgeFoundationError(f"Accepted relation lacks complete source review: {proposal['id']}")
        value["source_checked"] = True
        value["review"] = {name: deepcopy(review[name]) for name in PUBLISHED_REVIEW_FIELDS}
        published["relations"].append(value)
    validate(published, root=root, resolve=True)
    return published
