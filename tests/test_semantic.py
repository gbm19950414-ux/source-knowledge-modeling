"""Behavioral checks for source spans, semantic branches and profile evolution."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from source_knowledge_modeling.core import KnowledgeFoundationError, load_json, query as legacy_query
from source_knowledge_modeling.export import export_manifest as legacy_export
from source_knowledge_modeling.semantic import (
    DRAFT_VERSION, all_anchors, anchor_key, export_projection, impact_projection,
    new_projection, new_semantic_ledger, publish_semantic, query_projection,
    record_semantic_decision, render_projection, validate_projection,
)
from source_knowledge_modeling.reading_view import VIEW_VERSION, render_reading_view
from source_knowledge_modeling.cli import main as cli_main
from source_knowledge_modeling.core import digest_bytes

ROOT = Path(__file__).resolve().parents[1]
GARDEN = ROOT / "contracts/knowledge-foundation/v1/examples/community-garden.v1.json"


def _anchor(locator: str, text: str, phrase: str) -> dict:
    start = text.index(phrase)
    return {"locator": locator, "start": start, "end": start + len(phrase), "quote": phrase}


def _fixture():
    foundation = load_json(GARDEN)
    texts = {u["locator"]: u["text"] for u in load_json(ROOT / foundation["source"]["snapshot_path"])["units"]}
    screening = [{"locator": u["locator"], "status": "not_applicable" if u["kind"] == "heading" else "unmodeled", "reason": "Synthetic source awaiting structured review."} for u in foundation["units"]]
    base = new_projection(foundation, GARDEN.read_bytes(), screening)
    def part(pid, kind, locator, phrase):
        return {"id": pid, "kind": kind, "text": phrase, "anchors": [_anchor(locator, texts[locator], phrase)]}
    condition = part("dry", "condition", "line:3", "表土干燥且未来六小时没有降雨预报")
    action = part("water", "action", "line:3", "志愿者浇水")
    exception = part("rain", "exception", "line:4", "若预计下雨")
    alternative = part("postpone", "action", "line:4", "通常推迟浇水")
    def relation(rid, kind, from_id, to_id, locator, phrase):
        return {"id": rid, "type": kind, "from_id": from_id, "to_id": to_id, "anchors": [_anchor(locator, texts[locator], phrase)], "note": "Explicit source relation"}
    statement = {"id": "watering-branch", "summary": "干燥且无雨时浇水；下雨时通常推迟。", "origin_type": "source_claim", "modality": "recommended", "legacy_ids": ["a-water", "a-postpone"],
                 "elements": [condition, action, exception, alternative],
                 "relations": [relation("conditional-water", "conditions", "dry", "water", "line:3", "若表土干燥且未来六小时没有降雨预报，志愿者浇水"),
                               relation("rain-exception", "exception_to", "rain", "water", "line:4", "若预计下雨，通常推迟浇水"),
                               relation("rain-replaces", "replaces", "postpone", "water", "line:4", "若预计下雨，通常推迟浇水")],
                 "scientific_status": "not_assessed", "applicability_status": "not_assessed"}
    drafts = {"schema_version": DRAFT_VERSION, "artifact_id": base["artifact_id"], "base_revision_id": base["revision"]["id"],
              "proposals": [{"id": "p-branch", "retire_ids": [], "values": [statement]}]}
    return foundation, base, drafts


def _published():
    foundation, base, drafts = _fixture()
    ledger = new_semantic_ledger(drafts)
    checked = [anchor_key(a) for a in all_anchors(drafts["proposals"][0]["values"][0])]
    ledger = record_semantic_decision(base, drafts, ledger, proposal_id="p-branch", decision="accept", reviewer="synthetic reviewer", checked_anchors=checked, check_method="original_visual", note="Checked synthetic original", root=ROOT)
    return foundation, base, drafts, ledger, publish_semantic(base, drafts, ledger, revision_id="semantic-r1", root=ROOT)


def test_semantic_branch_review_query_view_and_export_agree():
    foundation, base, drafts, ledger, published = _published()
    assert validate_projection(base, root=ROOT, resolve=True)["unit_count"] == 5
    assert validate_projection(published, root=ROOT, resolve=True)["statement_count"] == 1
    found = query_projection(published, term="推迟", role="exception")
    branch = found["statements"][0]
    assert {r["type"] for r in branch["relations"]} == {"conditions", "exception_to", "replaces"}
    assert any(a["locator"] == "line:4" and a["quote"] == "若预计下雨" for e in branch["elements"] for a in e["anchors"])
    assert {x["locator"] for x in found["gaps"]} <= {a["locator"] for a in all_anchors(branch)}
    assert sum(found["gap_summary"].values()) >= len(found["gaps"])
    rendered = render_projection(published)
    export = export_projection(published, root=ROOT, producer_commit="synthetic-test")
    assert "rain-exception" in rendered and "若预计下雨" in rendered
    assert export["statements"] == [branch] and export["profile"] == found["profile"]
    assert [x["legacy_id"] for x in published["lineage"] if x["status"] == "partial"] == ["a-water", "a-postpone"]
    assert legacy_query(foundation, term="浇水")["annotations"]
    assert legacy_export(foundation, root=ROOT, producer_commit="synthetic-test")["schema_version"] == "source-knowledge-export/v1"


def test_offline_reading_view_preserves_a_non_training_branch_and_old_render(tmp_path):
    _, _, _, _, published = _published()
    artifact = tmp_path / "projection.json"
    artifact.write_text(json.dumps(published, ensure_ascii=False), encoding="utf-8")
    spec = {"schema_version": VIEW_VERSION, "artifact_id": published["artifact_id"],
            "revision_id": published["revision"]["id"], "artifact_sha256": digest_bytes(artifact.read_bytes()),
            "title": "社区花园浇水", "groups": [{"label": "浇水分支", "statement_ids": ["watering-branch"]}]}
    output = render_reading_view(published, artifact.read_bytes(), spec)
    assert render_reading_view(published, artifact.read_bytes(), spec) == output
    assert "<!doctype html>" in output and "__PAYLOAD__" not in output
    start = output.index('<script type="application/json" id="sm-data">') + len('<script type="application/json" id="sm-data">')
    end = output.index("</script>", start)
    payload = json.loads(output[start:end])
    assert payload["statements"][0]["elements"] == published["statements"][0]["elements"]
    assert payload["statements"][0]["relations"] == published["statements"][0]["relations"]
    assert payload["statements"][0]["review"] == published["statements"][0]["review"]
    assert payload["groups"] == [{"label": "浇水分支", "ids": ["watering-branch"]}]
    assert "primary_element_ids" not in payload["statements"][0]
    assert "主题总视图" in output
    spec_path = tmp_path / "view-spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    assert cli_main(["render", str(artifact), "--format", "html", "--view-spec", str(spec_path),
                     "--output", str(tmp_path / "view.html")]) == 0
    assert (tmp_path / "view.html").read_text(encoding="utf-8") == output
    assert cli_main(["render", str(artifact), "--output", str(tmp_path / "view.md")]) == 0
    assert "rain-exception" in (tmp_path / "view.md").read_text(encoding="utf-8")


def test_reading_view_rejects_stale_or_ambiguous_selection():
    _, _, _, _, published = _published()
    artifact_bytes = json.dumps(published, ensure_ascii=False).encode()
    spec = {"schema_version": VIEW_VERSION, "artifact_id": published["artifact_id"],
            "revision_id": published["revision"]["id"], "artifact_sha256": digest_bytes(artifact_bytes),
            "title": "社区花园浇水", "groups": [{"label": "浇水分支", "statement_ids": ["watering-branch"]}]}
    stale = deepcopy(spec)
    stale["artifact_sha256"] = "0" * 64
    with pytest.raises(KnowledgeFoundationError, match="SHA-256 mismatch"):
        render_reading_view(published, artifact_bytes, stale)
    duplicated = deepcopy(spec)
    duplicated["groups"].append(deepcopy(duplicated["groups"][0]))
    with pytest.raises(KnowledgeFoundationError, match="duplicate or unknown"):
        render_reading_view(published, artifact_bytes, duplicated)
    missing = deepcopy(spec)
    missing["groups"][0]["statement_ids"] = ["not-reviewed"]
    with pytest.raises(KnowledgeFoundationError, match="duplicate or unknown"):
        render_reading_view(published, artifact_bytes, missing)


def test_changed_draft_or_wrong_span_cannot_inherit_review():
    _, base, drafts, ledger, _ = _published()
    changed = deepcopy(drafts)
    changed["proposals"][0]["values"][0]["summary"] += " 未审阅文字"
    with pytest.raises(KnowledgeFoundationError, match="changed since review"):
        publish_semantic(base, changed, ledger, revision_id="semantic-tampered", root=ROOT)
    changed = deepcopy(drafts)
    changed["proposals"][0]["values"][0]["elements"][0]["anchors"][0]["quote"] = "不在原文的条件"
    with pytest.raises(KnowledgeFoundationError, match="quote mismatch"):
        publish_semantic(base, changed, ledger, revision_id="semantic-tampered", root=ROOT)
    checked = list({anchor_key(a) for a in all_anchors(drafts["proposals"][0]["values"][0])})[:-1]
    with pytest.raises(KnowledgeFoundationError, match="lacks checked source spans"):
        record_semantic_decision(base, drafts, new_semantic_ledger(drafts), proposal_id="p-branch", decision="accept", reviewer="R", checked_anchors=checked, check_method="original_visual", note="", root=ROOT)


def test_structural_direction_quantity_and_replacement_require_new_review():
    _, base, drafts, ledger, published = _published()
    broken = deepcopy(drafts)
    broken["proposals"][0]["values"][0]["relations"][0]["from_id"] = "postpone"
    with pytest.raises(KnowledgeFoundationError, match="direction/kind mismatch"):
        publish_semantic(base, broken, ledger, revision_id="semantic-bad", root=ROOT)
    broken = deepcopy(drafts)
    broken["proposals"][0]["values"][0]["elements"].append({"id": "missing-unit", "kind": "parameter", "text": "未指明的量", "anchors": broken["proposals"][0]["values"][0]["elements"][0]["anchors"]})
    with pytest.raises(KnowledgeFoundationError, match="Parameter must have"):
        publish_semantic(base, broken, ledger, revision_id="semantic-bad", root=ROOT)
    replacement = deepcopy(drafts["proposals"][0]["values"][0])
    replacement["id"] = "watering-branch-revised"
    replacement["summary"] = "干燥且无雨时浇水，下雨时推迟；含复核说明。"
    next_drafts = {"schema_version": DRAFT_VERSION, "artifact_id": published["artifact_id"], "base_revision_id": published["revision"]["id"],
                   "proposals": [{"id": "p-replace", "retire_ids": ["watering-branch"], "values": [replacement]}]}
    with pytest.raises(KnowledgeFoundationError, match="No accepted"):
        publish_semantic(published, next_drafts, new_semantic_ledger(next_drafts), revision_id="semantic-r2", root=ROOT)
    anchors = [anchor_key(a) for a in all_anchors(replacement) + all_anchors(published["statements"][0])]
    new_ledger = record_semantic_decision(published, next_drafts, new_semantic_ledger(next_drafts), proposal_id="p-replace", decision="accept", reviewer="new reviewer", checked_anchors=anchors, check_method="original_visual", note="Checked replacement", root=ROOT)
    revised = publish_semantic(published, next_drafts, new_ledger, revision_id="semantic-r2", root=ROOT)
    assert revised["supersessions"] == [{"from_revision": "semantic-r1", "old_ids": ["watering-branch"], "new_ids": ["watering-branch-revised"]}]
    assert published["statements"][0]["id"] == "watering-branch"


def test_profile_change_rescreens_unannotated_units_even_without_data_change():
    foundation, _, _, _, published = _published()
    # Represents a future semantic rule change; no source or statement bytes change.
    changed = deepcopy(published)
    changed["revision"] = {"id": "semantic-r2", "parent_id": "semantic-r1", "note": "New modeling rule"}
    changed["profile"]["definition_sha256"] = "0" * 64
    result = impact_projection(published, changed, foundation=foundation)
    assert result["profile_changed"] is True
    assert result["changed_statement_ids"] == []
    assert result["review_required_statement_ids"] == ["watering-branch"]
    assert result["source_reused"] is True
    assert result["rescreen_locators"] == [u["locator"] for u in foundation["units"]]
    assert "readonly_export" in result["affected_views"]


def test_split_merge_retains_old_revisions_and_requires_fresh_review():
    _, _, _, _, first = _published()
    original = first["statements"][0]
    split = []
    for suffix in ("condition", "exception"):
        item = deepcopy(original)
        item.pop("review")
        item["id"] = f"watering-{suffix}"
        item["summary"] = f"Separate {suffix} branch"
        split.append(item)
    drafts = {"schema_version": DRAFT_VERSION, "artifact_id": first["artifact_id"], "base_revision_id": "semantic-r1",
              "proposals": [{"id": "p-split", "retire_ids": ["watering-branch"], "values": split}]}
    with pytest.raises(KnowledgeFoundationError, match="No accepted"):
        publish_semantic(first, drafts, new_semantic_ledger(drafts), revision_id="semantic-r2", root=ROOT)
    checked = [anchor_key(a) for a in all_anchors(original) + [a for statement in split for a in all_anchors(statement)]]
    ledger = record_semantic_decision(first, drafts, new_semantic_ledger(drafts), proposal_id="p-split", decision="accept", reviewer="split reviewer", checked_anchors=checked, check_method="original_visual", note="Checked both branches", root=ROOT)
    second = publish_semantic(first, drafts, ledger, revision_id="semantic-r2", root=ROOT)
    assert {s["id"] for s in second["statements"]} == {"watering-condition", "watering-exception"}
    assert second["supersessions"][-1]["new_ids"] == ["watering-condition", "watering-exception"]
    assert first["statements"][0]["id"] == "watering-branch"

    merged = deepcopy(original)
    merged.pop("review")
    merged["id"] = "watering-merged"
    drafts3 = {"schema_version": DRAFT_VERSION, "artifact_id": first["artifact_id"], "base_revision_id": "semantic-r2",
               "proposals": [{"id": "p-merge", "retire_ids": ["watering-condition", "watering-exception"], "values": [merged]}]}
    with pytest.raises(KnowledgeFoundationError, match="No accepted"):
        publish_semantic(second, drafts3, new_semantic_ledger(drafts3), revision_id="semantic-r3", root=ROOT)
    checked3 = [anchor_key(a) for a in all_anchors(merged) + [a for statement in second["statements"] for a in all_anchors(statement)]]
    ledger3 = record_semantic_decision(second, drafts3, new_semantic_ledger(drafts3), proposal_id="p-merge", decision="accept", reviewer="merge reviewer", checked_anchors=checked3, check_method="original_visual", note="Checked merged meaning", root=ROOT)
    third = publish_semantic(second, drafts3, ledger3, revision_id="semantic-r3", root=ROOT)
    assert [s["id"] for s in third["statements"]] == ["watering-merged"]
    assert third["supersessions"][-1]["old_ids"] == ["watering-condition", "watering-exception"]
    assert len(second["statements"]) == 2


def test_pdf_acceptance_needs_original_visual_and_example_identity_is_reviewed():
    _, base, drafts = _fixture()
    base["source"]["kind"] = "pdf"
    checked = [anchor_key(a) for a in all_anchors(drafts["proposals"][0]["values"][0])]
    with pytest.raises(KnowledgeFoundationError, match="original source check"):
        record_semantic_decision(base, drafts, new_semantic_ledger(drafts), proposal_id="p-branch", decision="accept", reviewer="R", checked_anchors=checked, check_method="source_text", note="", root=ROOT)
    ledger = record_semantic_decision(base, drafts, new_semantic_ledger(drafts), proposal_id="p-branch", decision="accept", reviewer="R", checked_anchors=checked, check_method="original_visual", note="Checked source", root=ROOT)
    changed = deepcopy(drafts)
    changed["proposals"][0]["values"][0]["origin_type"] = "source_example"
    with pytest.raises(KnowledgeFoundationError, match="changed since review"):
        publish_semantic(base, changed, ledger, revision_id="semantic-r1", root=ROOT)


def test_changed_condition_and_step_order_invalidate_acceptance():
    _, base, drafts = _fixture()
    proposal = drafts["proposals"][0]["values"][0]
    proposal["relations"].append({"id": "synthetic-order", "type": "precedes", "from_id": "water", "to_id": "postpone",
                                  "anchors": [proposal["relations"][1]["anchors"][0]], "note": "Synthetic order for review binding"})
    checked = [anchor_key(a) for a in all_anchors(proposal)]
    ledger = record_semantic_decision(base, drafts, new_semantic_ledger(drafts), proposal_id="p-branch", decision="accept", reviewer="R", checked_anchors=checked, check_method="original_visual", note="Synthetic", root=ROOT)
    for change in ("condition", "order"):
        tampered = deepcopy(drafts)
        statement = tampered["proposals"][0]["values"][0]
        if change == "condition":
            statement["elements"] = [e for e in statement["elements"] if e["id"] != "dry"]
            statement["relations"] = [r for r in statement["relations"] if r["id"] != "conditional-water"]
        else:
            statement["relations"][-1]["from_id"], statement["relations"][-1]["to_id"] = "postpone", "water"
        with pytest.raises(KnowledgeFoundationError, match="changed since review"):
            publish_semantic(base, tampered, ledger, revision_id="semantic-r1", root=ROOT)
    cycle = deepcopy(drafts)
    cycle["proposals"][0]["values"][0]["relations"].append({"id": "reverse", "type": "precedes", "from_id": "postpone", "to_id": "water",
                                                        "anchors": [proposal["relations"][1]["anchors"][0]], "note": "Synthetic cycle"})
    with pytest.raises(KnowledgeFoundationError, match="Cyclic step order"):
        publish_semantic(base, cycle, ledger, revision_id="semantic-r1", root=ROOT)
