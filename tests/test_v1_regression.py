from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from source_knowledge_modeling.core import (
    KnowledgeFoundationError,
    coverage,
    digest_bytes,
    digest_text,
    impact,
    load_json,
    query,
    render_markdown,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
GARDEN = ROOT / "contracts/knowledge-foundation/v1/examples/community-garden.v1.json"


def test_cross_domain_conditions_exception_unknown_and_view_match_query() -> None:
    garden = load_json(GARDEN)
    validate(garden, root=ROOT, resolve=True)
    found = query(garden, term="浇水")
    rendered = render_markdown(garden)
    assert found["revision_id"] == garden["revision"]["id"]
    assert "未来六小时没有降雨预报" in rendered
    assert "若叶片萎蔫且表土仍干燥" in rendered
    assert "未给出单次浇水量" in rendered
    assert "```mermaid" in rendered
    assert rendered.count(" -->|") == len(garden["relations"])
    for annotation in found["annotations"]:
        assert annotation["id"] in rendered
        for locator in annotation["locators"]:
            assert locator in rendered
    returned_ids = {annotation["id"] for annotation in found["annotations"] + found["context_annotations"]}
    assert all(relation["from_id"] in returned_ids and relation["to_id"] in returned_ids for relation in found["relations"])


def test_missing_source_unit_and_unreviewed_reason_fail_explicitly() -> None:
    garden = load_json(GARDEN)
    broken = deepcopy(garden)
    broken["annotations"][0]["unit_ids"] = ["nonexistent"]
    with pytest.raises(KnowledgeFoundationError, match="Unknown unit"):
        validate(broken)
    broken = deepcopy(garden)
    broken["units"][1]["interpretation_status"] = "unreviewed"
    broken["units"][1]["review_note"] = ""
    with pytest.raises(KnowledgeFoundationError, match="needs a reason"):
        validate(broken)


def test_snapshot_revision_mismatch_is_not_silently_read(tmp_path: Path) -> None:
    garden = load_json(GARDEN)
    source = garden["source"]
    for relative in (source["path"], source["snapshot_path"]):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    snapshot_path = tmp_path / source["snapshot_path"]
    snapshot_path.write_text(snapshot_path.read_text() + " ", encoding="utf-8")
    with pytest.raises(KnowledgeFoundationError, match="revision mismatch"):
        validate(garden, root=tmp_path, resolve=True)


def test_source_and_annotation_revision_impacts_are_distinct(tmp_path: Path) -> None:
    old = load_json(GARDEN)
    revised = deepcopy(old)
    revised["revision"] = {"id": "r2-source", "parent_id": "r1", "note": "Synthetic source text edit."}
    source_text = (ROOT / revised["source"]["path"]).read_text(encoding="utf-8").replace("每周六", "每周日")
    source_path = tmp_path / revised["source"]["path"]
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8")
    snapshot = load_json(ROOT / revised["source"]["snapshot_path"])
    snapshot["units"][1]["text"] = snapshot["units"][1]["text"].replace("每周六", "每周日")
    snapshot_path = tmp_path / revised["source"]["snapshot_path"]
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    revised["source"]["sha256"] = digest_bytes(source_path.read_bytes())
    revised["source"]["snapshot_sha256"] = digest_bytes(snapshot_path.read_bytes())
    revised["units"][1]["content_sha256"] = digest_text(snapshot["units"][1]["text"])
    revised["annotations"][0]["summary"] = "协调员每周日查看苗床表土。"
    revised["annotations"][0]["conditions"] = ["每周日。"]
    validate(revised, root=tmp_path, resolve=True)
    source_impact = impact(old, revised)
    assert source_impact["changed_unit_ids"] == ["u2"]
    assert "a-check" in source_impact["affected_annotation_ids"]
    assert "r1" in source_impact["affected_relation_ids"]
    assert source_impact["affected_views"] == ["query", "learning_markdown"]
    # The published earlier revision remains readable with its original files.
    assert validate(old, root=ROOT, resolve=True)["revision_id"] == "r1"

    annotation_only = deepcopy(old)
    annotation_only["revision"] = {"id": "r2-annotation", "parent_id": "r1", "note": "Synthetic annotation edit."}
    annotation_only["annotations"][0]["summary"] += "（审阅说明修订）"
    validate(annotation_only, root=ROOT, resolve=True)
    annotation_impact = impact(old, annotation_only)
    assert annotation_impact["source_changed"] is False
    assert annotation_impact["changed_unit_ids"] == []
    assert annotation_impact["affected_annotation_ids"] == ["a-check"]
    assert annotation_impact["affected_relation_ids"] == ["r1"]


def test_conflicting_relations_are_retained_instead_of_first_match() -> None:
    garden = load_json(GARDEN)
    garden["relations"].append({
        "id": "r-conflict", "type": "contradicts", "from_id": "a-water", "to_id": "a-postpone",
        "support_unit_ids": ["u3", "u4"], "origin_type": "project_interpretation",
        "source_checked": True, "note": "合成边界测试，不是原文结论。",
    })
    validate(garden)
    assert "contradicts" in render_markdown(garden)
    assert any(relation["id"] == "r-conflict" for relation in query(garden)["relations"])
