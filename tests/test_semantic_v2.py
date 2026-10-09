"""V2 behavior across migration, independent checks, cross-package views and replay."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from source_knowledge_modeling.core import KnowledgeFoundationError, load_json
from source_knowledge_modeling.semantic import profile_manifest
from source_knowledge_modeling.cli import main as cli_main
from source_knowledge_modeling import semantic_v2 as v2
from source_knowledge_modeling.reading_view_v2 import render_reading_view_v2
from test_semantic import ROOT, GARDEN, _anchor, _published


def _migrated():
    _, _, _, _, old = _published()
    old_bytes = json.dumps(old, ensure_ascii=False, sort_keys=True).encode()
    return v2.migrate_v1(old, old_bytes, root=ROOT, revision_id="semantic-v2-migrated")


def _proposal(base, *, proposal_id="p-isolated"):
    snapshot = load_json(ROOT / base["source"]["snapshot_path"])
    text = next(x["text"] for x in snapshot["units"] if x["locator"] == "line:5")
    anchor = _anchor("line:5", text, "这份说明没有给出单次浇水量")
    element = v2.seal({"id": "missing-volume", "kind": "unresolved", "text": "原说明未给出单次浇水量", "origin_type": "source_claim", "modality": "asserted", "negated": True, "example_scope": None,
                       "anchors": [anchor], "context_ids": [], "source_check_ids": ["check-volume"], "review_state": "accepted", "review_id": None, "legacy_review_package_id": None}, "element")
    package = {"id": "source-limit", "summary": "说明的范围与缺项", "origin_type": "source_claim", "modality": "asserted", "element_ids": ["missing-volume"], "relation_ids": [], "context_ids": [], "legacy_review": None}
    proposal = {"id": proposal_id, "reason": "Source explicitly identifies an absent amount.", "upsert_elements": [element], "upsert_relations": [], "upsert_packages": [package],
                "upsert_information": [{"id": "info-volume", "locator": "line:5", "start": anchor["start"], "end": anchor["end"], "meaning": "The explanation does not give a per-watering amount", "status": "modeled", "model_refs": ["missing-volume"], "reason": "Explicit source absence represented without inventing a quantity."}],
                "retire_elements": [], "retire_relations": [], "retire_packages": [], "lineage": [], "rule_ids": ["IS2-04", "IS2-09"]}
    drafts = {"schema_version": v2.DRAFT_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"], "base_sha256": v2.digest(base), "proposals": [proposal]}
    return drafts, anchor


def test_v2_migration_preserves_historical_review_and_v1_bytes():
    _, _, _, _, old = _published()
    original = deepcopy(old)
    base = _migrated()
    assert old == original
    report = v2.validate_projection(base, root=ROOT, resolve=True)
    assert (report["element_count"], report["relation_count"], report["package_count"]) == (4, 3, 1)
    assert all(x["review_state"] == "legacy_historical" and x["review_id"] is None for x in base["elements"] + base["relations"])
    assert base["packages"][0]["legacy_review"]["proposal_sha256"] == old["statements"][0]["review"]["proposal_sha256"]


def test_v2_source_check_review_publication_and_resume_are_distinct():
    base = _migrated()
    drafts, anchor = _proposal(base)
    v2.validate_drafts(base, drafts, root=ROOT)
    checks = v2.new_source_check_ledger(base)
    checks = v2.record_source_check(base, checks, check_id="check-volume", anchors=[anchor], reviewer="synthetic reviewer", method="source_text", note="Read original text", root=ROOT)
    assert v2.record_source_check(base, checks, check_id="check-volume", anchors=[anchor], reviewer="synthetic reviewer", method="source_text", note="Read original text", root=ROOT) == checks
    ledger = v2.new_review_ledger(base, drafts)
    checked = [v2.anchor_key(anchor)]
    ledger = v2.record_decision(base, drafts, ledger, checks, proposal_id="p-isolated", decision="accept", reviewer="synthetic reviewer", checked_anchors=checked, check_method="source_text", note="Checked meaning", root=ROOT)
    assert v2.record_decision(base, drafts, ledger, checks, proposal_id="p-isolated", decision="accept", reviewer="synthetic reviewer", checked_anchors=checked, check_method="source_text", note="Checked meaning", root=ROOT) == ledger
    result = v2.publish(base, drafts, ledger, checks, revision_id="semantic-v2-reviewed", root=ROOT)
    assert result == v2.publish(base, drafts, ledger, checks, revision_id="semantic-v2-reviewed", root=ROOT)
    assert result["reviews"][0]["id"] == result["elements"][-1]["review_id"]
    assert result["source_checks"][0]["id"] == "check-volume"
    assert v2.validate_projection(result, root=ROOT, resolve=True)["information_status"] == {"modeled": 1}
    assert "missing-volume" in v2.render_markdown(result)
    assert v2.export(result, root=ROOT, producer_commit="synthetic-test")["artifact_schema_version"] == v2.VERSION
    changed = v2.impact(base, result)
    assert changed["changed_element_ids"] == ["missing-volume"]
    assert changed["reuse_element_count"] == 4
    with pytest.raises(KnowledgeFoundationError):
        broken = deepcopy(result)
        broken["elements"][-1]["text"] = "Invented amount"
        v2.validate_projection(broken, root=ROOT, resolve=True)


def test_v2_package_move_reuses_objects_and_query_closes_cross_package_relations():
    base = _migrated()
    original_revisions = {x["id"]: x["content_revision"] for x in base["elements"] + base["relations"]}
    old_package = deepcopy(base["packages"][0])
    old_package["element_ids"].remove("water")
    new_package = {"id": "watering-action", "summary": "浇水动作", "origin_type": "source_claim", "modality": "recommended", "element_ids": ["water"], "relation_ids": [], "context_ids": [], "legacy_review": None}
    proposal = {"id": "p-move", "reason": "Repackage an existing action without changing its meaning.", "upsert_elements": [], "upsert_relations": [], "upsert_packages": [old_package, new_package], "upsert_information": [], "retire_elements": [], "retire_relations": [], "retire_packages": [], "lineage": [], "rule_ids": ["IS2-01", "IS2-08"]}
    drafts = {"schema_version": v2.DRAFT_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"], "base_sha256": v2.digest(base), "proposals": [proposal]}
    checks = v2.new_source_check_ledger(base)
    ledger = v2.record_decision(base, drafts, v2.new_review_ledger(base, drafts), checks, proposal_id="p-move", decision="accept", reviewer="synthetic reviewer", checked_anchors=[], check_method="source_text", note="Checked package context", root=ROOT)
    moved = v2.publish(base, drafts, ledger, checks, revision_id="semantic-v2-repackaged", root=ROOT)
    assert {x["id"]: x["content_revision"] for x in moved["elements"] + moved["relations"]} == original_revisions
    found = v2.query(moved, package_id="watering-action")
    assert any(r["type"] == "conditions" and r["to_id"] == "water" for r in found["relations"])
    assert {"dry", "water"} <= {x["id"] for x in found["elements"]}
    assert v2.impact(base, moved)["changed_element_ids"] == []
    assert v2.impact(base, moved)["changed_package_ids"] == ["watering-action", "watering-branch"]
    artifact_bytes = json.dumps(moved, ensure_ascii=False, sort_keys=True).encode()
    spec = {"schema_version": v2.VIEW_VERSION, "artifact_id": moved["artifact_id"], "revision_id": moved["revision"]["id"],
            "artifact_sha256": sha256(artifact_bytes).hexdigest(), "title": "社区花园", "groups": [{"label": "浇水", "package_ids": ["watering-action"]}]}
    html = render_reading_view_v2(moved, artifact_bytes, spec)
    payload = json.loads(html.split('<script type="application/json" id="sm-data">', 1)[1].split("</script>", 1)[0])
    topic = payload["statements"][0]
    assert topic["primary_element_ids"] == ["water"]
    assert {"dry", "water"} <= {x["id"] for x in topic["elements"]}
    assert "主题总视图" in html


def test_v2_reading_view_is_bound_to_exact_revision_and_shows_isolated_element():
    base = _migrated()
    artifact_bytes = (json.dumps(base, ensure_ascii=False, sort_keys=True) + "\n").encode()
    spec = {"schema_version": v2.VIEW_VERSION, "artifact_id": base["artifact_id"], "revision_id": base["revision"]["id"], "artifact_sha256": sha256(artifact_bytes).hexdigest(), "title": "社区花园", "groups": [{"label": "浇水", "package_ids": ["watering-branch"]}]}
    rendered = render_reading_view_v2(base, artifact_bytes, spec)
    assert "knowledge-semantic-projection/v2" in rendered and "semantic-reading-view/v2" in rendered
    assert "v1 提案历史核对" in rendered
    with pytest.raises(KnowledgeFoundationError):
        render_reading_view_v2(base, artifact_bytes + b" ", spec)


def test_v2_changed_proposal_cannot_reuse_semantic_acceptance():
    base = _migrated()
    drafts, anchor = _proposal(base)
    checks = v2.record_source_check(base, v2.new_source_check_ledger(base), check_id="check-volume", anchors=[anchor], reviewer="synthetic reviewer", method="source_text", note="Read original text", root=ROOT)
    ledger = v2.record_decision(base, drafts, v2.new_review_ledger(base, drafts), checks, proposal_id="p-isolated", decision="accept", reviewer="synthetic reviewer", checked_anchors=[v2.anchor_key(anchor)], check_method="source_text", note="Checked meaning", root=ROOT)
    altered = deepcopy(drafts)
    altered["proposals"][0]["upsert_elements"][0]["text"] = "The source prescribes a specific amount"
    altered["proposals"][0]["upsert_elements"][0] = v2.seal(altered["proposals"][0]["upsert_elements"][0], "element")
    with pytest.raises(KnowledgeFoundationError):
        v2.publish(base, altered, ledger, checks, revision_id="semantic-v2-invalid", root=ROOT)


def test_v2_prepare_fingerprint_source_mapping_and_layout_impact():
    foundation = load_json(GARDEN)
    base = _migrated()
    profile = profile_manifest("instructional-source", "2.0.0")
    scope = {"included_locators": ["line:3", "line:4"], "context_locators": ["line:2"], "description": "Synthetic watering"}
    package = v2.prepare(foundation, GARDEN.read_bytes(), scope, profile, existing=base, existing_bytes=b"synthetic-base")
    assert package == v2.prepare(foundation, GARDEN.read_bytes(), scope, profile, existing=base, existing_bytes=b"synthetic-base")
    changed = deepcopy(foundation)
    changed["source"]["snapshot_sha256"] = "a" * 64
    with pytest.raises(KnowledgeFoundationError):
        v2.prepare(changed, GARDEN.read_bytes(), scope, profile, existing=base, existing_bytes=b"synthetic-base")
    mapped_scope = {**scope, "anchor_map": [{"old_locator": "line:3", "new_locator": "line:3", "reason": "Inspect changed source; do not inherit offsets."}]}
    mapped = v2.prepare(changed, GARDEN.read_bytes(), mapped_scope, profile, existing=base, existing_bytes=b"synthetic-base")
    assert any(task["status"] == "verify_anchor_mapping" for task in mapped["tasks"])
    artifact_bytes = json.dumps(base, ensure_ascii=False, sort_keys=True).encode()
    spec = {"schema_version": v2.VIEW_VERSION, "artifact_id": base["artifact_id"], "revision_id": base["revision"]["id"], "artifact_sha256": sha256(artifact_bytes).hexdigest(), "title": "Garden", "groups": [{"label": "Water", "package_ids": ["watering-branch"]}]}
    updated = {**spec, "title": "Garden, revised title"}
    view_change = v2.impact_view(spec, updated, base, artifact_bytes)
    assert view_change["knowledge_reused"] and view_change["semantic_reviews_reused"]
    assert view_change["rebuild_views"] == ["offline_html"]


def test_v2_visual_region_binds_frozen_native_structure():
    unit = {"locator": "pdf-page:2/block:4", "kind": "chart", "bbox": [0.1, 0.2, 0.8, 0.7], "raw_content": [{"type": "chart_body", "content": ""}]}
    region = {"locator": unit["locator"], "bbox": unit["bbox"], "native_kind": "chart", "native_structure_sha256": v2.digest(unit["raw_content"]), "interpretation_status": "unresolved", "note": "Curve-level meaning unverified."}
    v2._check_region(region, {unit["locator"]}, {unit["locator"]: unit})
    changed = deepcopy(region)
    changed["native_structure_sha256"] = "0" * 64
    with pytest.raises(KnowledgeFoundationError):
        v2._check_region(changed, {unit["locator"]}, {unit["locator"]: unit})


def test_v2_cli_retries_reuse_source_check_review_and_publication(tmp_path):
    base = _migrated()
    drafts, anchor = _proposal(base)
    paths = {name: tmp_path / f"{name}.json" for name in ("base", "drafts", "anchors", "checks", "review", "published")}
    paths["base"].write_text(json.dumps(base, ensure_ascii=False))
    paths["drafts"].write_text(json.dumps(drafts, ensure_ascii=False))
    paths["anchors"].write_text(json.dumps([anchor], ensure_ascii=False))
    check_args = ["source-check", str(paths["base"]), "--root", str(ROOT), "--ledger", str(tmp_path / "missing-checks.json"), "--anchors", str(paths["anchors"]), "--check-id", "check-volume", "--reviewer", "synthetic reviewer", "--check-method", "source_text", "--note", "Read original text", "--output", str(paths["checks"])]
    assert cli_main(check_args) == 0
    first_check = paths["checks"].read_bytes()
    assert cli_main(check_args) == 0 and paths["checks"].read_bytes() == first_check
    review_args = ["review", str(paths["base"]), "--root", str(ROOT), "--drafts", str(paths["drafts"]), "--ledger", str(tmp_path / "missing-review.json"), "--source-checks", str(paths["checks"]), "--proposal-id", "p-isolated", "--decision", "accept", "--reviewer", "synthetic reviewer", "--check-method", "source_text", "--checked-anchor", v2.anchor_key(anchor), "--note", "Checked meaning", "--output", str(paths["review"])]
    assert cli_main(review_args) == 0
    first_review = paths["review"].read_bytes()
    assert cli_main(review_args) == 0 and paths["review"].read_bytes() == first_review
    publish_args = ["publish", str(paths["base"]), "--root", str(ROOT), "--drafts", str(paths["drafts"]), "--ledger", str(paths["review"]), "--source-checks", str(paths["checks"]), "--revision-id", "semantic-v2-reviewed", "--output", str(paths["published"])]
    assert cli_main(publish_args) == 0
    first_published = paths["published"].read_bytes()
    assert cli_main(publish_args) == 0 and paths["published"].read_bytes() == first_published
    assert len(load_json(paths["published"])["reviews"]) == 1


def test_v2_not_stated_needs_checked_scope_and_replacement_needs_lineage():
    base = _migrated()
    item = {"id": "info-not-stated", "locator": "line:3", "start": 0, "end": 1, "meaning": "No fixed watering amount in the checked span", "status": "not_stated", "model_refs": [], "reason": "Checked lines 3–5 and found no amount."}
    base["information"].append(item)
    with pytest.raises(KnowledgeFoundationError):
        v2.validate_projection(base, root=ROOT, resolve=True)
    item["checked_scope"] = ["line:3", "line:4", "line:5"]
    assert v2.validate_projection(base, root=ROOT, resolve=True)["information_status"] == {"not_stated": 1}
    replacement = deepcopy(base["elements"][0])
    replacement["review_state"] = "accepted"
    replacement["review_id"] = None
    replacement["legacy_review_package_id"] = None
    replacement["source_check_ids"] = ["check-replacement"]
    replacement["text"] += " (clarified)"
    replacement = v2.seal(replacement, "element")
    proposal = {"id": "p-replace", "reason": "Change an existing meaning", "upsert_elements": [replacement], "upsert_relations": [], "upsert_packages": [], "upsert_information": [], "retire_elements": [], "retire_relations": [], "retire_packages": [], "lineage": [], "rule_ids": ["IS2-02"]}
    drafts = {"schema_version": v2.DRAFT_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"], "base_sha256": v2.digest(base), "proposals": [proposal]}
    with pytest.raises(KnowledgeFoundationError, match="lineage"):
        v2.validate_drafts(base, drafts, root=ROOT)
