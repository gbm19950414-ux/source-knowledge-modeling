"""Public v3 workspace lifecycle with independent, frozen synthetic judgments."""
from copy import deepcopy
from hashlib import sha256
import json
import shlex
from pathlib import Path
import pytest

from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import KnowledgeFoundationError, load_json
from source_knowledge_modeling.semantic import profile_manifest
from source_knowledge_modeling import semantic_v3 as v3
from source_knowledge_modeling import semantic_workflow as workflow
from source_knowledge_modeling.reading_view_v3 import render_reading_view_v3
from test_semantic import GARDEN, ROOT
from test_semantic_v2 import _migrated


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _workspace(tmp_path):
    scope = tmp_path / "scope.json"
    _write(scope, {"included_locators": ["line:5"], "description": "Synthetic absence of specified watering amount"})
    run = tmp_path / "run"
    command = ["prepare", str(GARDEN), "--root", str(ROOT), "--profile", "instructional-source@3.0.0",
               "--scope", str(scope), "--output-dir", str(run)]
    assert main(command) == main(command) == 0
    quote = "没有给出单次浇水量"
    anchor = {"locator": "line:5", "quote": quote}
    info = {"id": "meaning-amount", "meaning": "The source states no single-watering amount", "status": "required",
            "critical": True, "anchors": [anchor], "reason": "The source explicitly says the amount is absent."}
    discourse = {"id": "discourse-none", "meaning": "No semantic relation is required for the amount absence",
                 "status": "not_applicable", "critical": False, "anchors": [anchor],
                 "reason": "The selected claim is standalone in this synthetic slice."}
    identity = {"source_sha256": load_json(run / "base.json")["source"]["sha256"],
                "snapshot_sha256": load_json(run / "base.json")["source"]["snapshot_sha256"]}
    _write(run / "source-information.json", {"schema_version": "knowledge-source-information/v1", **identity,
                                             "reviewer": "synthetic fixture reviewer", "checked_at": "2026-09-24T00:00:00Z", "items": [info]})
    _write(run / "discourse-audit.json", {"schema_version": "knowledge-discourse-audit/v1", **identity,
                                          "reviewer": "synthetic fixture reviewer", "checked_at": "2026-09-24T00:00:00Z", "items": [discourse]})
    proposal = {"id": "p-amount", "reason": "Represent the explicit absence without inventing an amount.",
                "rule_ids": ["IS3-03", "IS3-05"],
                "upsert_elements": [{"id": "no-amount", "kind": "unresolved", "text": "No single-watering amount is specified",
                                     "origin_type": "source_claim", "modality": "asserted", "negated": True,
                                     "anchors": [anchor]}],
                "upsert_packages": [{"id": "amount-scope", "summary": "What the source does not quantify",
                                     "origin_type": "source_claim", "modality": "asserted", "element_ids": ["no-amount"],
                                     "relation_ids": [], "context_ids": [], "legacy_review": None}],
                "upsert_information": [{"id": info["id"], "anchor": anchor, "meaning": info["meaning"],
                                        "status": "modeled", "bindings": [{"object_id": "no-amount", "field_path": "/text"}],
                                        "reason": info["reason"]}],
                "upsert_discourse": [{"id": discourse["id"], "anchor": anchor, "meaning": discourse["meaning"],
                                      "status": "not_applicable", "bindings": [], "reason": discourse["reason"]}]}
    _write(run / "drafts.json", {"schema_version": v3.DRAFT_VERSION, "artifact_id": load_json(run / "base.json")["artifact_id"],
                                 "base_revision_id": "base", "proposals": [proposal]})
    return run, anchor


def test_v3_direct_prepare_and_honest_v2_migration():
    foundation = load_json(GARDEN)
    fresh = v3.new_projection(foundation, GARDEN.read_bytes(), {"included_locators": ["line:5"], "description": "Synthetic"},
                              profile_manifest("instructional-source", "3.0.0"), root=ROOT)
    assert fresh["legacy"] is None and fresh["source_units"][0]["order"] == 4
    old = _migrated()
    migrated = v3.migrate_v2(old, b"frozen v2 bytes", root=ROOT, foundation=foundation, revision_id="migrated-v3")
    assert all(x["review_state"] == "legacy_historical" and x["origin_type"] == "unresolved"
               for x in migrated["elements"] + migrated["relations"])
    assert migrated["elements"][0]["legacy_fields"]["modality"] == old["elements"][0]["modality"]
    assert migrated["legacy"]["review_history"] == old["reviews"]
    assert not v3.query(migrated, layer="source")["elements"]


def test_v3_public_assessment_publish_render_and_replay(tmp_path):
    run, anchor = _workspace(tmp_path)
    root_args = ["--root", str(ROOT)]
    assert main(["draft-import", str(run / "base.json"), *root_args, "--drafts", str(run / "drafts.json"),
                 "--output", str(run / "checked-drafts.json"), "--run-dir", str(run)]) == 0
    drafts = load_json(run / "checked-drafts.json")
    candidate = load_json(run / "candidate.json")
    targets = load_json(run / "review-targets.json")
    assert targets["schema_version"] == "knowledge-semantic-review-targets/v1"
    assert targets["draft_sha256"] == v3.digest(drafts)
    assert all(x["status"] == "pending" for x in targets["targets"])
    assert {(x["target_kind"], x["target_id"]) for x in targets["targets"]} == {
        (kind, item["id"]) for kind, plural in (("information", "information"), ("discourse", "discourse"),
                                                ("element", "elements"), ("relation", "relations"))
        for item in candidate[plural]}
    assert all(x["content_revision"] == next(y["content_revision"] for y in candidate[x["target_kind"]+"s"] if y["id"] == x["target_id"])
               for x in targets["targets"] if x["target_kind"] in {"element", "relation"})
    assert drafts["base_sha256"] == v3.digest(load_json(run / "base.json"))
    assert drafts["proposals"][0]["upsert_elements"][0]["content_revision"] == drafts["proposals"][0]["upsert_information"][0]["bindings"][0]["content_revision"]
    assert main(["assess", str(run), *root_args,
                 "--output", str(run / "quality-before-review.json")]) == 0
    assert load_json(run / "quality-before-review.json")["status"] == "fail"
    _write(run / "semantic-assessment.json", {"schema_version": "knowledge-semantic-assessment/v1",
                                              "draft_sha256": v3.digest(drafts),
                                              "inventory_sha256": sha256((run / "source-information.json").read_bytes()).hexdigest(),
                                              "discourse_sha256": sha256((run / "discourse-audit.json").read_bytes()).hexdigest(),
                                              "reviewer": "synthetic independent reviewer", "reviewer_type": "human", "model": "unknown",
                                              "check_method": "source_text", "checked_at": "2026-09-24T00:01:00Z",
                                              "checks": [{"target_kind": "information", "target_id": "meaning-amount", "verdict": "pass", "reason": "Exact absence retained."},
                                                         {"target_kind": "discourse", "target_id": "discourse-none", "verdict": "not_applicable", "reason": "No relation required in selected slice."},
                                                         {"target_kind": "element", "target_id": "no-amount", "verdict": "pass", "reason": "Negation and scope checked.",
                                                          "content_revision": drafts["proposals"][0]["upsert_elements"][0]["content_revision"]}]})
    expectations = tmp_path / "expectations.json"
    _write(expectations, {"schema_version": "knowledge-semantic-expectations/v1",
                          "source_sha256": load_json(run / "base.json")["source"]["sha256"],
                          "cases": [{"id": "absence", "collection": "elements", "where": {"id": "no-amount"},
                                     "require": [{"path": "/text", "contains": "No single-watering amount"},
                                                 {"path": "/negated", "equals": True}]}]})
    assert main(["assess", str(run), *root_args, "--expectations", str(expectations),
                 "--output", str(run / "quality-report.json")]) == 0
    assert load_json(run / "quality-report.json")["status"] == "pass"
    _write(run / "anchors.json", [v3.resolve_anchor(load_json(run / "base.json"), anchor, root=ROOT)])
    _write(run / "regions.json", [])
    assert main(["source-check", str(run / "base.json"), *root_args, "--ledger", str(run / "source-checks.json"),
                 "--anchors", str(run / "anchors.json"), "--regions", str(run / "regions.json"),
                 "--check-id", "source-amount", "--reviewer", "synthetic source reviewer", "--check-method", "source_text",
                 "--output", str(run / "source-checks.json"), "--run-dir", str(run)]) == 0
    commands = [shlex.split(line) for line in (run / "RUNBOOK.md").read_text().splitlines() if line.startswith("skm ")]
    assess_command = next(c for c in commands if c[1] == "assess" and "--inputs-only" not in c)
    assert assess_command[assess_command.index("--expectations") + 1] == str(run / "inputs/expectations.json")
    source_command = next(c for c in commands if c[1] == "source-check")
    assert source_command[source_command.index("--reviewer") + 1] == "synthetic source reviewer"
    assert source_command[source_command.index("--check-id") + 1] == "source-amount"
    saved_check = (run / "source-checks.json").read_bytes()
    saved_report = (run / "quality-report.json").read_bytes()
    # Execute the documented commands: preserve expectations and actual event identity.
    assert main(assess_command[1:]) == main(source_command[1:]) == 0
    assert (run / "source-checks.json").read_bytes() == saved_check
    assert (run / "quality-report.json").read_bytes() == saved_report
    key = v3.anchor_key(json.loads((run / "anchors.json").read_text())[0])
    assert main(["review", str(run / "base.json"), *root_args, "--drafts", str(run / "checked-drafts.json"),
                 "--ledger", str(run / "review-ledger.json"), "--source-checks", str(run / "source-checks.json"),
                 "--assessment", str(run / "quality-report.json"), "--proposal-id", "p-amount", "--decision", "accept",
                 "--reviewer", "synthetic semantic reviewer", "--check-method", "source_text", "--checked-anchor", key,
                 "--output", str(run / "reviews/p-amount.json"), "--run-dir", str(run)]) == 0
    assert main(["publish", str(run / "base.json"), *root_args, "--drafts", str(run / "checked-drafts.json"),
                 "--ledger", str(run / "reviews/p-amount.json"), "--source-checks", str(run / "source-checks.json"),
                 "--assessment", str(run / "quality-report.json"), "--revision-id", "published-v3",
                 "--output", str(run / "published.json"), "--run-dir", str(run)]) == 0
    published = load_json(run / "published.json")
    assert v3.validate_projection(published, root=ROOT, resolve=True)["element_count"] == 1
    assert published["screening"] == [{"locator": "line:5", "status": "modeled", "reason": "Inventoried meanings and connections have field-level model bindings."}]
    html = render_reading_view_v3(published, (run / "published.json").read_bytes())
    assert 'semantic-reading-view/v3' in html and '按原文顺序查看要素出现位置' in html
    assert main(["render", str(run / "published.json"), *root_args, "--format", "html",
                 "--output", str(run / "reading.html"), "--run-dir", str(run)]) == 0
    assert main(["impact", str(run / "base.json"), *root_args, "--new", str(run / "published.json"),
                 "--output", str(run / "impact.json"), "--run-dir", str(run)]) == 0
    assert main(["replay", str(run / "run-manifest.json"), *root_args, "--output-dir", str(tmp_path / "replay")]) == 0
    assert (tmp_path / "replay/published.json").read_bytes() == (run / "published.json").read_bytes()
    assert (tmp_path / "replay/quality-before-review.json").read_bytes() == (run / "quality-before-review.json").read_bytes()
    assert (tmp_path / "replay/candidate.json").read_bytes() == (run / "candidate.json").read_bytes()
    assert (tmp_path / "replay/review-targets.json").read_bytes() == (run / "review-targets.json").read_bytes()
    records = load_json(run / "run-manifest.json")["records"]
    assert set(next(r for r in records if r["command"] == "draft-import")["derived"]) == {"candidate", "review_targets"}
    assessments = [r for r in records if r["command"] == "assess"]
    assert len(assessments) == 2
    assert assessments[0]["input_snapshots"]["semantic_assessment"]["sha256"] != assessments[1]["input_snapshots"]["semantic_assessment"]["sha256"]
    altered = deepcopy(published)
    altered["source_checks"][0]["source_sha256"] = "0" * 64
    with pytest.raises(KnowledgeFoundationError, match="wrong source"):
        v3.validate_projection(altered, root=ROOT, resolve=True)
    altered = deepcopy(published)
    altered["elements"][0]["source_check_ids"].append("missing-check")
    with pytest.raises(KnowledgeFoundationError, match="lacks independent"):
        v3.validate_projection(altered, root=ROOT, resolve=True)


def test_v3_source_quote_ambiguity_requires_offsets():
    foundation = load_json(GARDEN)
    base = v3.new_projection(foundation, GARDEN.read_bytes(), {"included_locators": ["line:5"], "description": "Synthetic"},
                             profile_manifest("instructional-source", "3.0.0"), root=ROOT)
    with pytest.raises(KnowledgeFoundationError, match="ambiguous"):
        v3.resolve_anchor(base, {"locator": "line:5", "quote": "没有"}, root=ROOT)
    assert v3.resolve_anchor(base, {"locator": "line:5", "quote": "没有", "start": 4, "end": 6}, root=ROOT)["start"] == 4
    for start, end in ((-1, 6), (True, 6), (4, 400)):
        with pytest.raises(KnowledgeFoundationError, match="offset mismatch"):
            v3.resolve_anchor(base, {"locator": "line:5", "quote": "没有", "start": start, "end": end}, root=ROOT)


def test_v3_normalization_source_reads_do_not_grow_with_anchor_count(tmp_path, monkeypatch):
    run, _ = _workspace(tmp_path)
    base, authored = map(load_json, (run / "base.json", run / "drafts.json"))
    source_paths = {(ROOT / base["source"][key]).resolve() for key in ("path", "snapshot_path")}
    reads = {path: 0 for path in source_paths}
    read_bytes = Path.read_bytes

    def counted(path):
        if path.resolve() in reads:
            reads[path.resolve()] += 1
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    single = v3.normalize_drafts(base, authored, root=ROOT)
    single_reads = reads.copy()
    reads.update({path: 0 for path in reads})
    proposal = authored["proposals"][0]
    prototype = proposal["upsert_elements"][0]
    proposal["upsert_elements"] = [{**deepcopy(prototype), "id": f"claim-{i}"} for i in range(64)]
    proposal["upsert_packages"][0]["element_ids"] = [x["id"] for x in proposal["upsert_elements"]]
    proposal["upsert_information"][0]["bindings"][0]["object_id"] = "claim-0"
    many = v3.normalize_drafts(base, authored, root=ROOT)
    assert len(single["proposals"][0]["upsert_occurrences"]) == 1
    assert len(many["proposals"][0]["upsert_occurrences"]) == 64
    assert reads == single_reads
    assert all(0 < count < 10 for count in reads.values())


@pytest.mark.parametrize("changed_key", ["path", "snapshot_path"])
def test_v3_anchor_verification_is_scoped_to_one_operation(tmp_path, monkeypatch, changed_key):
    run, anchor = _workspace(tmp_path)
    base = load_json(run / "base.json")
    changed_path = (ROOT / base["source"][changed_key]).resolve()
    snapshot_path = (ROOT / base["source"]["snapshot_path"]).resolve()
    read_bytes, reads = Path.read_bytes, []

    def counted(path):
        reads.append(path.resolve())
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    resolve = v3.anchor_resolver(base, root=ROOT)
    first = resolve(anchor)
    assert reads.count(snapshot_path) == 1  # Verified and parsed from the same read.

    def changed(path):
        value = read_bytes(path)
        return value + b" " if path.resolve() == changed_path else value

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert resolve(anchor) == first  # This operation uses its verified snapshot.
    with pytest.raises(KnowledgeFoundationError, match="source hash mismatch"):
        v3.resolve_anchor(base, anchor, root=ROOT)  # The next operation rechecks.


def test_v3_prepare_resume_checks_frozen_inputs(tmp_path):
    run, _ = _workspace(tmp_path)
    (run / "inputs/scope.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(KnowledgeFoundationError, match="frozen input changed"):
        workflow.prepare_workspace(GARDEN, root=ROOT,
                                   profile=profile_manifest("instructional-source", "3.0.0"),
                                   scope_path=tmp_path / "scope.json", output_dir=run)


def test_v3_compound_expectation_requires_one_coherent_object():
    candidate = {"source": {"sha256": "a" * 64}, "elements": [{"id": "one", "text": "alpha"},
                                                              {"id": "two", "text": "beta"}]}
    expectation = {"schema_version": "knowledge-semantic-expectations/v1",
                   "cases": [{"id": "whole-claim", "collection": "elements",
                              "require": [{"path": "/text", "contains": "alpha", "match": "any"},
                                          {"path": "/text", "contains": "beta", "match": "any"}]}]}
    assert workflow.expectation_checks(candidate, expectation)[0]["status"] == "fail"


def test_v3_expectation_uses_all_source_anchors_for_cross_unit_meaning():
    candidate = {"source": {"sha256": "a" * 64}, "elements": [],
                 "information": [{"id": "cross-page", "locator": "page:1", "meaning": "Claim continues", "status": "modeled"}]}
    expectation = {"schema_version": "knowledge-semantic-expectations/v1",
                   "cases": [{"id": "second-page", "collection": "information",
                              "where": {"locator": "page:2", "status": "modeled"},
                              "require": [{"path": "/meaning", "contains": "continues"}]}]}
    assert workflow.expectation_checks(candidate, expectation)[0]["status"] == "fail"
    audits = {"information": {"items": [{"id": "cross-page", "meaning": "Claim continues",
                                          "anchors": [{"locator": "page:1"}, {"locator": "page:2"}]}]}}
    assert workflow.expectation_checks(candidate, expectation, audits)[0]["status"] == "pass"
    audits["information"]["items"][0]["meaning"] = "Different claim"
    assert workflow.expectation_checks(candidate, expectation, audits)[0]["status"] == "fail"


def test_v3_critical_confirmed_source_silence_is_reviewable(tmp_path):
    run, _ = _workspace(tmp_path)
    discourse = load_json(run / "discourse-audit.json")
    discourse["items"][0].update({"status": "not_stated", "critical": True,
                                   "checked_scope": ["line:5"],
                                   "reason": "Inspected the selected source unit; no relation is asserted."})
    _write(run / "discourse-audit.json", discourse)
    authored = load_json(run / "drafts.json")
    authored["proposals"][0]["upsert_discourse"][0].update({"status": "not_stated", "checked_scope": ["line:5"]})
    _write(run / "drafts.json", authored)
    assert main(["draft-import", str(run / "base.json"), "--root", str(ROOT),
                 "--drafts", str(run / "drafts.json"), "--output", str(run / "checked-drafts.json"),
                 "--run-dir", str(run)]) == 0
    drafts = load_json(run / "checked-drafts.json")
    candidate = v3.candidate_preview(load_json(run / "base.json"), drafts, root=ROOT)
    _write(run / "semantic-assessment.json", {"schema_version": "knowledge-semantic-assessment/v1",
                                              "draft_sha256": v3.digest(drafts),
                                              "inventory_sha256": sha256((run / "source-information.json").read_bytes()).hexdigest(),
                                              "discourse_sha256": sha256((run / "discourse-audit.json").read_bytes()).hexdigest(),
                                              "reviewer": "independent synthetic AI", "reviewer_type": "AI", "model": "unknown",
                                              "check_method": "source_text", "checked_at": "2026-09-24T00:04:00Z",
                                              "checks": [{"target_kind": "information", "target_id": "meaning-amount", "verdict": "pass", "reason": "Absence preserved."},
                                                         {"target_kind": "discourse", "target_id": "discourse-none", "verdict": "not_stated", "reason": "Checked source silence."},
                                                         {"target_kind": "element", "target_id": "no-amount", "verdict": "pass", "reason": "Negated author assertion preserved.",
                                                          "content_revision": candidate["elements"][0]["content_revision"]}]})
    assert main(["assess", str(run), "--root", str(ROOT), "--output", str(run / "quality-report.json")]) == 0
    assert load_json(run / "quality-report.json")["status"] == "pass"


@pytest.mark.parametrize("mutation, requirement", [
    ("reversed_text", {"path": "/text", "contains": "No single-watering amount"}),
    ("lost_negation", {"path": "/negated", "equals": True}),
    ("wrong_origin", {"path": "/origin_type", "equals": "source_claim"}),
])
def test_v3_frozen_expectation_detects_valid_semantic_mutations(tmp_path, mutation, requirement):
    run, _ = _workspace(tmp_path)
    authored = load_json(run / "drafts.json")
    element = authored["proposals"][0]["upsert_elements"][0]
    if mutation == "reversed_text":
        element["text"] = "A fixed single-watering amount is specified"
    elif mutation == "lost_negation":
        element["negated"] = False
    else:
        element["origin_type"] = "project_interpretation"
    _write(run / "drafts.json", authored)
    assert main(["draft-import", str(run / "base.json"), "--root", str(ROOT), "--drafts", str(run / "drafts.json"),
                 "--output", str(run / "checked-drafts.json"), "--run-dir", str(run)]) == 0
    drafts = load_json(run / "checked-drafts.json")
    candidate = v3.candidate_preview(load_json(run / "base.json"), drafts, root=ROOT)
    assert v3.validate_projection(candidate, root=ROOT, resolve=True)["element_count"] == 1
    _write(run / "semantic-assessment.json", {"schema_version": "knowledge-semantic-assessment/v1",
                                              "draft_sha256": v3.digest(drafts),
                                              "inventory_sha256": sha256((run / "source-information.json").read_bytes()).hexdigest(),
                                              "discourse_sha256": sha256((run / "discourse-audit.json").read_bytes()).hexdigest(),
                                              "reviewer": "synthetic adversarial reviewer", "reviewer_type": "AI", "model": "unknown",
                                              "check_method": "source_text", "checked_at": "2026-09-24T00:02:00Z",
                                              "checks": [{"target_kind": "information", "target_id": "meaning-amount", "verdict": "pass", "reason": "Incorrectly accepted."},
                                                         {"target_kind": "discourse", "target_id": "discourse-none", "verdict": "not_applicable", "reason": "No relation."},
                                                         {"target_kind": "element", "target_id": "no-amount", "verdict": "pass", "reason": "Incorrectly accepted.",
                                                          "content_revision": candidate["elements"][0]["content_revision"]}]})
    expectations = tmp_path / "expectations.json"
    _write(expectations, {"schema_version": "knowledge-semantic-expectations/v1",
                          "source_sha256": candidate["source"]["sha256"],
                          "cases": [{"id": "absence", "collection": "elements", "where": {"id": "no-amount"},
                                     "require": [requirement]}]})
    assert main(["assess", str(run), "--root", str(ROOT), "--expectations", str(expectations),
                 "--output", str(run / "quality-report.json")]) == 0
    report = load_json(run / "quality-report.json")
    assert report["status"] == "fail"
    assert next(x for x in report["checks"] if x["id"] == "expectation:absence")["status"] == "fail"


def test_v3_source_order_cross_package_isolated_and_interpretation_layer():
    foundation = load_json(GARDEN)
    scope = {"included_locators": ["line:2", "line:3", "line:5"], "description": "Synthetic cross-topic contract"}
    projection = v3.new_projection(foundation, GARDEN.read_bytes(), scope,
                                   profile_manifest("instructional-source", "3.0.0"), root=ROOT)
    def element(ident, kind, text, locator, quote):
        return {"id": ident, "kind": kind, "text": text, "origin_type": "source_claim", "modality": "asserted",
                "negated": False, "example_scope": None,
                "anchors": [v3.resolve_anchor(projection, {"locator": locator, "quote": quote}, root=ROOT)],
                "context_ids": [], "source_check_ids": [], "review_state": "pending_semantic",
                "review_id": None, "legacy_review_package_id": None}
    condition = v3.seal(element("z-dry", "condition", "The topsoil is dry and rain is not forecast", "line:3", "若表土干燥且未来六小时没有降雨预报"), "element")
    action = v3.seal(element("a-water", "action", "Water the beds", "line:3", "志愿者浇水"), "element")
    assertion = v3.seal(element("b-no-volume", "assertion", "No single watering amount is given", "line:5", "没有给出单次浇水量"), "element")
    isolated = v3.seal(element("c-check", "action", "Inspect topsoil", "line:2", "协调员查看每块苗床的表土"), "element")
    projection["elements"] = [action, assertion, isolated, condition]
    relation = {"id": "source-condition", "type": "conditions", "from_id": condition["id"], "to_id": action["id"],
                "from_revision": condition["content_revision"], "to_revision": action["content_revision"],
                "origin_type": "source_claim", "basis": "explicit", "premises": [], "rationale": "", "uncertainty": "",
                "note": "The source states the watering condition.", "anchors": condition["anchors"] + action["anchors"],
                "context_ids": [], "source_check_ids": [], "review_state": "pending_semantic",
                "review_id": None, "legacy_review_package_id": None}
    interpretation = {"id": "project-amount-scope", "type": "qualifies", "from_id": assertion["id"], "to_id": action["id"],
                      "from_revision": assertion["content_revision"], "to_revision": action["content_revision"],
                      "origin_type": "project_interpretation", "basis": "project_interpretation",
                      "premises": [assertion["id"]], "rationale": "The source does not quantify the watering action.",
                      "uncertainty": "The scope of the missing amount may need more context.", "note": "Project reading only.",
                      "anchors": assertion["anchors"], "context_ids": [assertion["id"]],
                      "source_check_ids": [], "review_state": "pending_semantic", "review_id": None,
                      "legacy_review_package_id": None}
    projection["relations"] = [v3.seal(relation, "relation"), v3.seal(interpretation, "relation")]
    projection["packages"] = [
        {"id": "watering", "summary": "Watering branch", "origin_type": "source_claim", "modality": "asserted",
         "element_ids": [action["id"], condition["id"]], "relation_ids": ["source-condition", "project-amount-scope"], "context_ids": [], "legacy_review": None},
        {"id": "source-limit", "summary": "Source limit", "origin_type": "source_claim", "modality": "asserted",
         "element_ids": [assertion["id"]], "relation_ids": [], "context_ids": [], "legacy_review": None},
        {"id": "inspection", "summary": "Inspect first", "origin_type": "source_claim", "modality": "asserted",
         "element_ids": [isolated["id"]], "relation_ids": [], "context_ids": [], "legacy_review": None}]
    projection["occurrences"] = v3._occurrences(projection["elements"], projection["source_units"])
    assert v3.validate_projection(projection, root=ROOT, resolve=True)["element_count"] == 4
    source = v3.query(projection, package_id="watering", layer="source")
    all_layers = v3.query(projection, package_id="watering", layer="all")
    assert [x["id"] for x in source["elements"]] == ["z-dry", "a-water"]
    assert [x["id"] for x in source["relations"]] == ["source-condition"]
    assert "b-no-volume" in {x["id"] for x in all_layers["elements"]}
    assert "project-amount-scope" in {x["id"] for x in all_layers["relations"]}
    assert len(v3.query(projection, package_id="inspection")["elements"]) == 1
    data = (json.dumps(projection, ensure_ascii=False, sort_keys=True) + "\n").encode()
    page = render_reading_view_v3(projection, data)
    payload = json.loads(page.split('<script type="application/json" id="sm-data">', 1)[1].split("</script>", 1)[0])
    assert payload["statements"][0]["source_order_ids"][:2] == ["z-dry", "a-water"]
    assert payload["statements"][0]["primary_element_ids"] == ["a-water", "z-dry"]
    assert "作者来源层" in page and "含项目解释" in page


def test_v3_normalization_binding_uses_revised_relation_revision(monkeypatch):
    # Focus this regression on choosing the just-sealed upsert over the old relation.
    export = load_json(ROOT / 'contracts/semantic-discourse/v1/examples/garden.export.json')
    base = {k: deepcopy(v) for k,v in export.items() if k not in
            {'producer','artifact_schema_version','artifact_revision_id'}}
    base.update(schema_version=v3.VERSION,legacy=None,
                revision={'id':export['artifact_revision_id'],'parent_id':None,'note':'Synthetic regression base.'})
    old = next(x for x in base['relations'] if x['id']=='dry-conditions-water')
    changed = deepcopy(old)
    changed.pop('content_revision')
    changed['note'] = 'Synthetic revised exact relation note.'
    authored = {'schema_version':v3.DRAFT_VERSION,'artifact_id':base['artifact_id'],
                'base_revision_id':base['revision']['id'],
                'proposals':[{'id':'p-revise-relation','reason':'Synthetic relation revision',
                              'upsert_relations':[changed],
                              'upsert_information':[{'id':'audit-relation','locator':'line:3','start':0,'end':17,
                                                     'meaning':'Synthetic relation field binding','status':'modeled',
                                                     'bindings':[{'object_id':old['id'],'field_path':'/note'}],
                                                     'reason':'Synthetic regression'}]}]}
    monkeypatch.setattr(v3,'validate_drafts',lambda *args,**kwargs:None)
    normalized=v3.normalize_drafts(base,authored,root=ROOT)
    proposal=normalized['proposals'][0]
    new_revision=proposal['upsert_relations'][0]['content_revision']
    binding_revision=proposal['upsert_information'][0]['bindings'][0]['content_revision']
    assert new_revision != old['content_revision']
    assert binding_revision == new_revision


def _visual_inventory_workspace(tmp_path):
    """Frozen synthetic caption plus an empty native image, with no real book data."""
    original, anchor = _workspace(tmp_path / 'author')
    foundation = load_json(GARDEN)
    source = ROOT / foundation['source']['path']
    (tmp_path / 'source.txt').write_bytes(source.read_bytes())
    snapshot = load_json(ROOT / foundation['source']['snapshot_path'])
    image = {'locator': 'figure:1', 'text': '', 'kind': 'image',
             'bbox': [0.1, 0.2, 0.8, 0.9], 'raw_content': [{'type': 'image_body', 'content': ''}]}
    snapshot['units'].append(image)
    _write(tmp_path / 'snapshot.json', snapshot)
    foundation['source'].update(path='source.txt', snapshot_path='snapshot.json',
                                snapshot_sha256=workflow.file_hash(tmp_path / 'snapshot.json'))
    foundation['units'].append({'id': 'figure', 'locator': image['locator'], 'order': 5, 'kind': 'image',
                                'content_sha256': None,
                                'transcription_status': 'visual_only', 'interpretation_status': 'unreviewed',
                                'review_note': 'Synthetic empty visual.'})
    foundation['annotations'] = []; foundation['relations'] = []
    for unit in foundation['units']:
        unit['interpretation_status'] = 'unreviewed'
    _write(tmp_path / 'foundation.json', foundation)
    _write(tmp_path / 'scope.json', {'included_locators': ['line:5', 'figure:1'], 'description': 'Caption and native image'})
    run = tmp_path / 'visual-run'
    assert main(['prepare', str(tmp_path / 'foundation.json'), '--root', str(tmp_path),
                 '--profile', 'instructional-source@3.0.0', '--scope', str(tmp_path / 'scope.json'),
                 '--output-dir', str(run)]) == 0
    region = json.loads((run / 'visual-regions.json').read_text())[0]
    assert region['interpretation_status'] == 'unresolved'
    region.update(interpretation_status='interpreted', note='Synthetic fixture native image inspected.')
    base = load_json(run / 'base.json')
    for filename in ('source-information.json', 'discourse-audit.json'):
        audit = load_json(original / filename)
        audit.update(source_sha256=base['source']['sha256'], snapshot_sha256=base['source']['snapshot_sha256'])
        audit['items'][0]['regions'] = [region]
        _write(run / filename, audit)
    drafts = load_json(original / 'drafts.json')
    drafts['proposals'][0]['upsert_elements'][0]['regions'] = [region]
    _write(run / 'drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path),
                 '--drafts', str(run / 'drafts.json'), '--output', str(run / 'checked-drafts.json')]) == 0
    return run, region


def test_public_assessment_native_visual_inventory(tmp_path):
    run, region = _visual_inventory_workspace(tmp_path)
    # A separate source-only image entry needs no invented OCR quote.
    info = load_json(run / 'source-information.json')
    info['items'].append({**info['items'][0], 'id': 'image-only', 'anchors': [], 'regions': [region]})
    _write(run / 'source-information.json', info)
    assert not workflow.schema_errors(info, 'knowledge-source-audit-v1.schema.json')
    report_path = run / 'visual-quality.json'
    assert main(['assess', str(run), '--root', str(tmp_path), '--output', str(report_path)]) == 0
    checks = {x['id']: x for x in load_json(report_path)['checks']}
    for ident in ('primary-scope', 'information:format', 'information:anchors:image-only',
                  'information:anchors:meaning-amount', 'discourse:anchors:discourse-none'):
        assert checks[ident]['status'] == 'pass'
    # Frozen region identity is not a new semantic acceptance or a field binding.
    assert checks['information:image-only']['status'] == 'fail'
    assert checks['assessment:actor']['status'] == 'fail'


@pytest.mark.parametrize('mutation', ['hash', 'bbox', 'kind', 'unknown', 'outside_scope', 'empty'])
def test_public_assessment_visual_evidence_negatives(tmp_path, mutation):
    run, region = _visual_inventory_workspace(tmp_path)
    audit = load_json(run / 'source-information.json')
    item = audit['items'][0]
    if mutation == 'hash': item['regions'][0]['native_structure_sha256'] = '0' * 64
    elif mutation == 'bbox': item['regions'][0]['bbox'] = [0, 0, 1, 1]
    elif mutation == 'kind': item['regions'][0]['native_kind'] = 'table'
    elif mutation == 'unknown': item['regions'][0]['locator'] = 'not-in-snapshot'
    elif mutation == 'outside_scope': item['regions'][0]['locator'] = 'line:1'
    else: item['anchors'] = []; item['regions'] = []
    _write(run / 'source-information.json', audit)
    report = run / 'invalid-visual-quality.json'
    assert main(['assess', str(run), '--root', str(tmp_path), '--output', str(report)]) == 0
    checks = {x['id']: x for x in load_json(report)['checks']}
    assert checks['information:anchors:meaning-amount']['status'] == 'fail'
    assert checks['primary-scope']['status'] == 'fail'
    assert load_json(report)['status'] == 'fail'


def test_visual_inventory_original_hash_is_rechecked(tmp_path):
    run, _ = _visual_inventory_workspace(tmp_path)
    (tmp_path / 'source.txt').write_text('Replaced original source')
    with pytest.raises(KnowledgeFoundationError, match='hash mismatch'):
        workflow.assess_workspace(run, root=tmp_path)


def test_visual_audit_field_binding_without_fictional_text_offsets(tmp_path):
    run, region = _visual_inventory_workspace(tmp_path)
    info = load_json(run / 'source-information.json')
    visual_item = {**info['items'][0], 'id': 'visual-meaning', 'anchors': [], 'regions': [region]}
    info['items'].append(visual_item)
    _write(run / 'source-information.json', info)
    drafts = load_json(run / 'drafts.json')
    drafts['proposals'][0]['upsert_information'].append({
        'id': 'visual-meaning', 'region': region, 'meaning': visual_item['meaning'],
        'status': 'modeled', 'bindings': [{'object_id': 'no-amount', 'field_path': '/text'}],
        'reason': 'Synthetic structural binding fixture; semantic acceptance still required.'})
    _write(run / 'visual-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'visual-drafts.json'), '--output', str(run / 'visual-checked.json')]) == 0
    canonical = load_json(run / 'visual-checked.json')
    item = canonical['proposals'][0]['upsert_information'][-1]
    assert item['locator'] == 'figure:1' and item['region'] == region
    assert 'start' not in item and 'end' not in item
    assert item['bindings'][0]['content_revision'] == canonical['proposals'][0]['upsert_elements'][0]['content_revision']
    # Removing the actual visual object evidence keeps the authored structure legal,
    # but destroys the claimed visual meaning binding.
    drafts['proposals'][0]['upsert_elements'][0].pop('regions')
    _write(run / 'caption-only-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'caption-only-drafts.json'), '--output', str(run / 'caption-only-checked.json')]) == 2


def test_unresolved_visual_audit_cannot_claim_modeled(tmp_path):
    run, region = _visual_inventory_workspace(tmp_path)
    drafts = load_json(run / 'drafts.json')
    region['interpretation_status'] = 'unresolved'
    item = drafts['proposals'][0]['upsert_information'][0]
    item.pop('anchor'); item['region'] = region
    _write(run / 'unresolved-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'unresolved-drafts.json'), '--output', str(run / 'unresolved-checked.json')]) == 2


def _visual_note_drafts(tmp_path, kind='element'):
    run, region = _visual_inventory_workspace(tmp_path)
    region['note'] = 'The synthetic diagram has two separate branches, with no connecting arrow.'
    drafts = load_json(run / 'drafts.json')
    proposal = drafts['proposals'][0]
    proposal['upsert_elements'][0]['regions'] = [region]
    object_id = 'no-amount'
    if kind == 'relation':
        detail = deepcopy(proposal['upsert_elements'][0])
        detail.update(id='visual-detail', kind='assertion', negated=False,
                      text='Synthetic fixture detail used to exercise relation evidence.')
        proposal['upsert_elements'].append(detail)
        proposal['upsert_relations'] = [{
            'id': 'visual-relation', 'type': 'elaborates', 'from_id': 'visual-detail',
            'to_id': 'no-amount', 'origin_type': 'source_claim', 'basis': 'explicit',
            'anchors': detail['anchors'], 'regions': [region]}]
        proposal['upsert_packages'][0]['element_ids'].append('visual-detail')
        proposal['upsert_packages'][0]['relation_ids'].append('visual-relation')
        object_id = 'visual-relation'
    proposal['upsert_information'].append({
        'id': 'visual-note', 'region': region, 'meaning': region['note'],
        'status': 'modeled', 'bindings': [{'object_id': object_id, 'field_path': '/regions/0/note'}],
        'reason': 'Bind the interpreted meaning once, rather than duplicating it in object text.'})
    info = load_json(run / 'source-information.json')
    info['items'].append({**info['items'][0], 'id': 'visual-note', 'meaning': region['note'],
                          'anchors': [], 'regions': [region]})
    _write(run / 'source-information.json', info)
    return run, drafts, object_id


@pytest.mark.parametrize('kind', ['element', 'relation'])
def test_public_import_binds_interpreted_visual_note_once(tmp_path, kind):
    run, drafts, object_id = _visual_note_drafts(tmp_path, kind)
    _write(run / 'note-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'note-drafts.json'), '--output', str(run / 'note-checked.json'),
                 '--run-dir', str(run)]) == 0
    candidate = load_json(run / 'candidate.json')
    obj = next(x for x in candidate[kind + 's'] if x['id'] == object_id)
    binding = candidate['information'][-1]['bindings'][0]
    assert binding['field_path'] == '/regions/0/note'
    assert binding['content_revision'] == obj['content_revision']
    assert obj['regions'][0]['note'] not in obj.get('text', '')
    assert main(['assess', str(run), '--root', str(tmp_path),
                 '--output', str(run / 'note-quality.json')]) == 0
    checks = {x['id']: x for x in load_json(run / 'note-quality.json')['checks']}
    assert checks['information:anchors:visual-note']['status'] == 'pass'
    # A valid field location never manufactures an independent semantic judgment.
    assert checks['information:visual-note']['status'] == 'fail'
    assert load_json(run / 'note-quality.json')['status'] == 'fail'


@pytest.mark.parametrize('path', [
    '/regions', '/regions/0', '/regions/0/locator', '/regions/0/bbox',
    '/regions/0/native_structure_sha256', '/regions/0/interpretation_status',
    '/regions/-1/note', '/regions/00/note', '/regions/+0/note',
    '/regions/\u0660/note', '/regions/1/note', '/regions/0/note/extra',
])
def test_public_import_rejects_visual_metadata_and_invalid_note_paths(tmp_path, path):
    run, drafts, _ = _visual_note_drafts(tmp_path)
    drafts['proposals'][0]['upsert_information'][-1]['bindings'][0]['field_path'] = path
    _write(run / 'invalid-note-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'invalid-note-drafts.json'), '--output', str(run / 'invalid-checked.json')]) == 2


@pytest.mark.parametrize('mutation', ['unresolved', 'blank'])
def test_public_import_rejects_uninterpreted_or_blank_bound_note(tmp_path, mutation):
    run, drafts, _ = _visual_note_drafts(tmp_path)
    region = drafts['proposals'][0]['upsert_elements'][0]['regions'][0]
    if mutation == 'unresolved':
        region['interpretation_status'] = 'unresolved'
    else:
        region['note'] = '   '
    _write(run / 'invalid-note-drafts.json', drafts)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(tmp_path), '--drafts',
                 str(run / 'invalid-note-drafts.json'), '--output', str(run / 'invalid-checked.json')]) == 2
