"""Fictional garden review from existing synthetic test, not real human approval."""
from copy import deepcopy

import json

from pathlib import Path

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

if __name__ == '__main__':
    _, _, _, _, projection = _published()
    out = ROOT / 'demo-output'
    out.mkdir(exist_ok=True)
    artifact = out / 'projection.json'
    artifact.write_text(json.dumps(projection, ensure_ascii=False, indent=2), encoding='utf-8')
    spec = {'schema_version': VIEW_VERSION, 'artifact_id': projection['artifact_id'],
            'revision_id': projection['revision']['id'], 'artifact_sha256': digest_bytes(artifact.read_bytes()),
            'title': 'Synthetic garden · 社区花园', 'groups': [{'label': '浇水分支（虚构审阅）', 'statement_ids': ['watering-branch']}]}
    (out / 'view-spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'garden.html').write_text(render_reading_view(projection, artifact.read_bytes(), spec), encoding='utf-8')
    print('Generated offline synthetic semantic reading view; no real review or user approval claimed.')
