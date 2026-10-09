"""Bounded, reviewer-attested dependency proofs and exact prior judgment reuse.

Mechanical consistency is verified here. Natural-language completeness and the
truth of an actor's inspection remain that actor's responsibility.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import KnowledgeFoundationError, load_json
from . import semantic_v3 as v3

CERT_VERSION = "knowledge-semantic-dependency-certificate/v1"
REUSE_VERSION = "knowledge-semantic-assessment-reuse/v1"
DIMENSIONS = ("claim_voice", "definition_context", "conditions_exceptions", "endpoints_premises",
              "examples_quantities", "audit_fields", "silence_negative_claims")
_VERIFICATION_TOKEN = object()
KINDS = (("element", "elements"), ("relation", "relations"),
         ("information", "information"), ("discourse", "discourse"))


def _fail(message: str) -> None:
    raise KnowledgeFoundationError("Dependency/reuse proof: " + message)


def targets(projection: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(kind, item["id"]): item for kind, plural in KINDS for item in projection[plural]}


def target_stamp(kind: str, item: dict[str, Any]) -> str:
    if kind in ("element", "relation"):
        return v3.digest({k: item[k] for k in ("id", "content_revision", "anchors", "regions") if k in item})
    return v3.digest(item)


def semantic_state(projection: dict[str, Any]) -> str:
    return v3.digest({"identity": {k: projection[k] for k in
        ("artifact_id", "source", "profile", "scope", "source_units", "packages", "screening", "occurrences")},
        "targets": [{"kind": kind, "id": ident, "stamp": target_stamp(kind, item)}
                    for (kind, ident), item in sorted(targets(projection).items())]})


def mandatory_edges(base: dict[str, Any]) -> dict[str, set[str]]:
    objects = {x["id"]: x for x in base["elements"] + base["relations"]}
    edges = {ident: set(item["context_ids"]) | set(item.get("premises", [])) for ident, item in objects.items()}
    for relation in base["relations"]:
        ends = {relation["from_id"], relation["to_id"]}
        edges[relation["id"]].update(ends)
        for ident in ends:
            edges[ident].add(relation["id"])
    for package in base["packages"]:
        for ident in package["element_ids"] + package["relation_ids"]:
            edges[ident].update(set(package["context_ids"]) - {ident})
    for audit in base["information"] + base["discourse"]:
        ids = {b["object_id"] for b in audit["bindings"]}
        for ident in ids:
            edges[ident].update(ids - {ident})
    return edges


def object_facts(base: dict[str, Any], kind: str, item: dict[str, Any]) -> dict[str, Any]:
    ident = item["id"]
    return {"kind": kind, "id": ident, "content_revision": item["content_revision"],
            "review_id": item["review_id"], "source_check_ids": item["source_check_ids"],
            "anchor_keys": sorted(v3.anchor_key(a) for a in item["anchors"]),
            "region_keys": sorted(v3.region_key(r) for r in item.get("regions", [])),
            "occurrences_sha256": v3.digest([x for x in base["occurrences"] if x["element_id"] == ident]),
            "audits": [{"kind": ak, "id": audit["id"], "sha256": v3.digest(audit),
                        "bindings": [b for b in audit["bindings"] if b["object_id"] == ident]}
                       for ak in ("information", "discourse") for audit in base[ak]
                       if any(b["object_id"] == ident for b in audit["bindings"])]}


def _statement(statement: dict[str, Any], base: dict[str, Any], *, required_locators: set[str] | None = None) -> None:
    actor = statement["reviewer"]
    if not actor["actor"].strip() or not actor["checked_at"].strip():
        _fail("actual reviewer identity/time missing")
    if base["source"]["kind"] == "pdf" and actor["method"] != "original_visual":
        _fail("PDF dependency inspection requires original_visual")
    evidence = set(base["scope"]["included_locators"] + base["scope"].get("context_locators", []))
    required = required_locators or set()
    covered = set(statement["covered_locators"])
    if not covered <= evidence or not required <= covered:
        _fail("inspection coverage omits its own evidence or leaves the declared source scope")
    for dim in DIMENSIONS:
        row = statement["dimensions"][dim]
        if not row["reason"].strip() or not row["checked_scope"] or not set(row["checked_scope"]) <= evidence or not required <= set(row["checked_scope"]):
            _fail("dependency dimension lacks a precise inspection scope/reason: " + dim)


@dataclass(frozen=True)
class VerifiedCertificate:
    # Instances are returned only after exact old publication reconstruction.
    base_sha256: str
    certificate: dict[str, Any]
    _verification_token: object
    _certificate_sha256: str


def verify_prior_run(prior_run: Path, accepted: dict[str, Any], *, root: Path) -> dict[str, Any]:
    from . import semantic_workflow as workflow
    manifest = load_json(prior_run / "run-manifest.json")
    workflow.check_schema(manifest, "knowledge-semantic-run-v1.schema.json")
    if manifest["inputs"]["guide_sha256"] != workflow.guide_hash():
        _fail("prior guide is incompatible")
    if manifest["tool"] != workflow._tool_identity() or manifest["inputs"].get("tool_sha256") != workflow._tool_identity()["implementation_sha256"]:
        _fail("prior producer/resource identity is incompatible with the current implementation")
    if manifest["profile"] != accepted["profile"] or manifest["inputs"].get("profile_sha256") != accepted["profile"]["definition_sha256"]:
        _fail("prior profile identity is incompatible with the accepted projection")
    records = manifest["records"]
    publications = [r for r in records if r["command"] == "publish"]
    if not publications:
        _fail("complete prior public publication record is required")
    record = publications[-1]
    recorded_publication = workflow._resolve_ref(record["output"], prior_run, root)
    if not recorded_publication.is_file() or v3.digest(load_json(recorded_publication)) != v3.digest(accepted):
        _fail("prior recorded published output is not the accepted projection")
    for name in ("artifact", "drafts", "ledger", "source_checks", "assessment"):
        if name not in record["inputs"]:
            _fail("prior publication lacks " + name)
    # Verify all immutable frozen transaction evidence, including the actual
    # authored assessment version used before acceptance.
    refs = list(manifest["prepare_inputs"].values())
    for row in records:
        refs.append(row["output"])
        refs.extend(row.get("derived", {}).values())
        refs.extend(row.get("input_snapshots", row["inputs"]).values())
        refs.extend(ref for ref in row["inputs"].values() if ref["location"] == "root")
    for ref in refs:
        path = workflow._resolve_ref(ref, prior_run, root)
        if not path.is_file() or workflow.file_hash(path) != ref["sha256"]:
            _fail("prior frozen evidence changed: " + ref["path"])
    paths = {k: workflow._resolve_ref(r, prior_run, root) for k, r in record["inputs"].items()}
    if any(workflow.file_hash(paths[k]) != record["inputs"][k]["sha256"] for k in
           ("artifact", "drafts", "ledger", "source_checks", "assessment")):
        _fail("prior publication input bytes changed")
    quality = workflow.verify_assessment(prior_run, paths["assessment"], root=root)
    if quality["status"] != "pass" or quality["input_hashes"]["semantic_assessment"] != workflow.file_hash(prior_run / "semantic-assessment.json"):
        _fail("prior quality is not an exact recomputable pass")
    # Bounded first release: no recursively inherited semantic reuse chains.
    if "reuse_provenance" in quality["input_hashes"]:
        _fail("recursive prior reuse chains are outside the first-release contract")
    base, drafts, ledger, checks = (load_json(paths[k]) for k in ("artifact", "drafts", "ledger", "source_checks"))
    rebuilt = v3.publish(base, drafts, ledger, checks, quality,
                         revision_id=record["arguments"]["revision_id"], root=root)
    if v3.digest(rebuilt) != v3.digest(accepted):
        _fail("prior public chain does not reconstruct the accepted projection")
    all_objects = {x["id"] for x in accepted["elements"] + accepted["relations"]}
    newly_reviewed = {x["id"] for p in drafts["proposals"] for x in p["upsert_elements"] + p["upsert_relations"]}
    if all_objects != newly_reviewed:
        _fail("prior run must itself freshly review every accepted object")
    review = load_json(prior_run / "semantic-assessment.json")
    return {"manifest_sha256": workflow.file_hash(prior_run / "run-manifest.json"),
            "quality_sha256": workflow.file_hash(paths["assessment"]),
            "assessment_sha256": workflow.file_hash(prior_run / "semantic-assessment.json"),
            "inventory_sha256": workflow.file_hash(prior_run / "source-information.json"),
            "discourse_sha256": workflow.file_hash(prior_run / "discourse-audit.json"),
            "published_sha256": v3.digest(accepted), "producer_sha256": manifest["tool"]["implementation_sha256"],
            "review": review, "checks": checks}


def base_binding(base: dict[str, Any]) -> dict[str, Any]:
    from . import semantic_workflow as workflow
    return {"base_sha256": v3.digest(base), "revision_id": base["revision"]["id"],
            "guide_sha256": workflow.guide_hash(),
            "contract_sha256": workflow.file_hash(workflow.RESOURCES / "schemas" / "knowledge-semantic-dependency-certificate-v1.schema.json"),
            **{k + "_sha256": v3.digest(base[k]) for k in
               ("source", "profile", "scope", "source_units", "occurrences", "packages", "screening", "information", "discourse")}}


def verify_certificate(base: dict[str, Any], certificate: dict[str, Any], *, root: Path,
                       prior_run: Path) -> VerifiedCertificate:
    from . import semantic_workflow as workflow
    workflow.check_schema(certificate, "knowledge-semantic-dependency-certificate-v1.schema.json")
    v3.validate_projection(base, root=root, resolve=True)
    if certificate["binding"] != base_binding(base):
        _fail("certificate base/source/profile/scope/guide/contract binding differs")
    prior = verify_prior_run(prior_run, base, root=root)
    if certificate["prior"] != {k: prior[k] for k in certificate["prior"]}:
        _fail("certificate prior-publication evidence differs")
    objects = {x["id"]: (kind, x) for kind, plural in KINDS[:2] for x in base[plural]}
    rows = {x["id"]: x for x in certificate["objects"]}
    if len(rows) != len(certificate["objects"]) or set(rows) != set(objects):
        _fail("certificate must cover every object exactly once")
    mandatory = mandatory_edges(base)
    for ident, (kind, item) in objects.items():
        row = rows[ident]
        if item["review_state"] != "accepted" or row["facts"] != object_facts(base, kind, item):
            _fail("object revision/evidence/audit/accepted identity differs: " + ident)
        _statement(row["inspection"], base, required_locators={a["locator"] for a in item["anchors"] + item.get("regions", [])})
        deps = {d["object_id"]: d for d in row["dependencies"]}
        if len(deps) != len(row["dependencies"]) or not mandatory[ident] <= set(deps) or not set(deps) <= set(objects) or ident in deps:
            _fail("missing mandatory, duplicate, self, or absent dependency: " + ident)
        for dep_id, dep in deps.items():
            dep_kind, dep_obj = objects[dep_id]
            if dep["kind"] != dep_kind or dep["content_revision"] != dep_obj["content_revision"] or not dep["reason"].strip() or not dep["evidence_locators"] or not set(dep["evidence_locators"]) <= set(base["scope"]["included_locators"] + base["scope"].get("context_locators", [])):
                _fail("dependency lacks exact revision/evidence/reason: " + ident)
    _statement(certificate["scope_inspection"], base)
    _statement(certificate["delta_inspection"], base)
    if set(certificate["scope_inspection"]["covered_locators"]) != set(base["scope"]["included_locators"] + base["scope"].get("context_locators", [])):
        _fail("complete source-first scope inspection is missing")
    return VerifiedCertificate(v3.digest(base), deepcopy(certificate), _VERIFICATION_TOKEN, v3.digest(certificate))


def _normal_audit(item: dict[str, Any], objects: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(item)
    for binding in result["bindings"]:
        if binding["object_id"] not in objects or binding["content_revision"] != objects[binding["object_id"]]["content_revision"]:
            _fail("audit binding is not an exact current object revision")
        binding["content_revision"] = "mechanical-revision-sync"
    return result


def plan_delta(base: dict[str, Any], candidate: dict[str, Any], verified: VerifiedCertificate) -> dict[str, Any]:
    if (not isinstance(verified, VerifiedCertificate) or verified._verification_token is not _VERIFICATION_TOKEN
            or verified.base_sha256 != v3.digest(base) or verified._certificate_sha256 != v3.digest(verified.certificate)):
        _fail("runtime verified certificate does not bind this base")
    cert = verified.certificate
    before, after = targets(base), targets(candidate)
    reasons = []
    for key in ("source", "profile", "scope", "source_units", "occurrences", "packages", "screening"):
        if base[key] != candidate[key]:
            reasons.append(key + "_changed")
    if set(before) != set(after):
        reasons.append("target_coverage_changed")
    old_objects = {x["id"]: x for x in base["elements"] + base["relations"]}
    new_objects = {x["id"]: x for x in candidate["elements"] + candidate["relations"]}
    for key in ("information", "discourse"):
        if [_normal_audit(x, old_objects) for x in base[key]] != [_normal_audit(x, new_objects) for x in candidate[key]]:
            reasons.append(key + "_meaning_or_coverage_changed")
    for ident in old_objects.keys() & new_objects.keys():
        old, new = old_objects[ident], new_objects[ident]
        for key in ("kind", "type", "origin_type", "modality", "negated", "example_scope", "quantity", "anchors", "regions", "context_ids", "premises", "from_id", "to_id", "basis"):
            if old.get(key) != new.get(key):
                reasons.append("object_classification_evidence_or_topology_changed")
                break
    if mandatory_edges(base) != mandatory_edges(candidate):
        reasons.append("mechanical_dependency_topology_changed")
    inspections = [cert["scope_inspection"], cert["delta_inspection"]] + [x["inspection"] for x in cert["objects"]]
    if any(x["status"] != "complete" for x in inspections):
        reasons.append("semantic_dependency_completeness_unknown")
    if cert["candidate_sha256"] != semantic_state(candidate):
        reasons.append("delta_inspection_candidate_changed")
    changed = {ident for ident in old_objects.keys() | new_objects.keys()
               if old_objects.get(ident, {}).get("content_revision") != new_objects.get(ident, {}).get("content_revision")}
    affected = set(changed)
    if reasons:
        affected = set(new_objects)
    else:
        edges = {x["id"]: {d["object_id"] for d in x["dependencies"]} for x in cert["objects"]}
        while True:
            extended = affected | {ident for ident, deps in edges.items() if deps & affected}
            if extended == affected:
                break
            affected = extended
    fresh = set()
    for (kind, ident), item in after.items():
        if reasons or kind in ("element", "relation") and ident in affected or kind in ("information", "discourse") and any(b["object_id"] in affected for b in item["bindings"]):
            fresh.add((kind, ident))
        elif (kind, ident) not in before or target_stamp(kind, before[(kind, ident)]) != target_stamp(kind, item):
            fresh.add((kind, ident))
    result = {"schema_version": "knowledge-semantic-review-plan/v1", "mode": "full_scope" if reasons else "certified_partial",
              "base_sha256": v3.digest(base), "candidate_sha256": semantic_state(candidate), "certificate_sha256": v3.digest(cert),
              "changed_object_ids": sorted(changed), "affected_object_ids": sorted(affected),
              "fallback_reasons": sorted(set(reasons)),
              "targets": [{"target_kind": k, "target_id": i, "mode": "fresh-required" if (k, i) in fresh else "reused", "stamp": target_stamp(k, x)} for (k, i), x in sorted(after.items())],
              "counts": {"total": len(after), "fresh": len(fresh), "reused": len(after) - len(fresh)}}
    result["plan_sha256"] = v3.digest(result)
    return result


def _prior_checks(prior: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(c['target_kind'], c['target_id']): c for c in prior['review']['checks']}


def reuse_inputs(run_dir: Path) -> dict[str, Path]:
    """Freeze every proof byte through ordinary public transaction snapshots."""
    paths = [run_dir / name for name in ('dependency-certificate.json', 'review-plan.json',
              'prior-accepted.json', 'reuse-template.json', 'reuse-provenance.json', 'source-checks-reused.json')]
    prior_dir = run_dir / 'prior-evidence'
    if prior_dir.exists():
        paths.extend(sorted(p for p in prior_dir.rglob('*') if p.is_file()))
    return {'reuse_input_' + str(n): path for n, path in enumerate(paths) if path.is_file()}


def prepare_assessment_reuse(run_dir: Path, *, root: Path, prior_run: Path,
                             prior_projection_path: Path, certificate_path: Path) -> dict[str, Any]:
    from . import semantic_workflow as workflow
    run_dir, prior_run = run_dir.resolve(), prior_run.resolve()
    if run_dir == prior_run or run_dir.is_relative_to(prior_run) or (prior_run.is_relative_to(run_dir) and prior_run != run_dir / 'prior-evidence'):
        _fail('prior and current workspaces must be separate and nonnested')
    base = load_json(run_dir / 'base.json')
    accepted = load_json(prior_projection_path)
    if accepted != base:
        _fail('accepted prior projection is not the current base')
    drafts = load_json(run_dir / 'checked-drafts.json')
    candidate = v3.candidate_preview(base, drafts, root=root)
    certificate = load_json(certificate_path)
    verified = verify_certificate(base, certificate, root=root, prior_run=prior_run)
    plan = plan_delta(base, candidate, verified)
    if plan['mode'] != 'certified_partial':
        _fail('partial reuse is unavailable: ' + ', '.join(plan['fallback_reasons']))
    prior = verify_prior_run(prior_run, accepted, root=root)
    if workflow.file_hash(run_dir / 'source-information.json') != prior['inventory_sha256'] or workflow.file_hash(run_dir / 'discourse-audit.json') != prior['discourse_sha256']:
        _fail('source-first independent inventory bytes changed')
    old_targets, old_checks = targets(base), _prior_checks(prior)
    if set(old_targets) != set(old_checks):
        _fail('prior assessment target coverage differs')
    rows, judgments = [], []
    old_actor = {k: prior['review'][k] for k in ('reviewer','reviewer_type','model','check_method','checked_at')}
    for target in plan['targets']:
        key = target['target_kind'], target['target_id']
        old_check = old_checks[key]
        obj = old_targets[key]
        row = {**target, 'prior_stamp': target_stamp(key[0], obj),
               'prior_check_sha256': v3.digest(old_check), 'prior_revision_id': base['revision']['id'],
               'prior_review_id': obj.get('review_id'), 'prior_reviewer': old_actor,
               'check_sha256': None, 'reviewer': None, 'recheck_rationale': None}
        if target['mode'] == 'reused':
            if row['stamp'] != row['prior_stamp']:
                _fail('unchanged target identity differs')
            judgments.append(deepcopy(old_check))
            row.update(check_sha256=v3.digest(old_check), reviewer=old_actor)
        else:
            judgment = {'target_kind': key[0], 'target_id': key[1], 'verdict': 'pending',
                        'reason': 'Pending actual fresh independent source meaning review.'}
            if key[0] in ('element', 'relation'):
                judgment['content_revision'] = targets(candidate)[key]['content_revision']
            judgments.append(judgment)
        rows.append(row)
    assessment = {**{k: prior['review'][k] for k in ('schema_version','reviewer_type','model','check_method')},
                  'draft_sha256': v3.digest(drafts), 'inventory_sha256': prior['inventory_sha256'],
                  'discourse_sha256': prior['discourse_sha256'], 'reviewer': '', 'checked_at': '', 'checks': judgments}
    provenance = {'schema_version': REUSE_VERSION, 'base_sha256': v3.digest(base),
                  'candidate_sha256': semantic_state(candidate), 'draft_sha256': v3.digest(drafts),
                  'inventory_sha256': prior['inventory_sha256'], 'discourse_sha256': prior['discourse_sha256'],
                  'assessment_sha256': None, 'certificate_sha256': v3.digest(certificate),
                  'plan_sha256': plan['plan_sha256'], 'prior': certificate['prior'],
                  'status': 'pending', 'targets': rows}
    workflow.check_schema(provenance, 'knowledge-semantic-assessment-reuse-v1.schema.json')
    # Copy only regular files. Never follow symlinks or let the proof reach out
    # of its bounded prior workspace; reference resolution independently checks root.
    frozen = run_dir / 'prior-evidence'
    prior_files = sorted(p for p in prior_run.rglob('*') if p.is_file())
    if any(p.is_symlink() or not p.resolve().is_relative_to(prior_run) for p in prior_files):
        _fail('prior evidence contains a symlink/path escape')
    for path in prior_files:
        out = frozen / path.relative_to(prior_run)
        out.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_immutable(out, path.read_bytes())
    for name, value in (('dependency-certificate.json', certificate), ('review-plan.json', plan),
                        ('prior-accepted.json', accepted), ('reuse-template.json', provenance),
                        ('source-checks-reused.json', prior['checks'])):
        workflow.write_immutable(run_dir / name, value)
    # Only the editable assessment template is replaced; no pass is invented.
    assessment_path = run_dir / 'semantic-assessment.json'
    current = load_json(assessment_path)
    if current.get('checks') and current != assessment:
        _fail('refusing to replace an already authored assessment')
    assessment_path.write_bytes(workflow.json_bytes(assessment))
    return provenance


def _same_prior_judgment(check: dict[str, Any], run_dir: Path, key: tuple[str, str]) -> bool:
    prior_review = load_json(run_dir / 'prior-evidence' / 'semantic-assessment.json')
    old = next((row for row in prior_review['checks'] if (row['target_kind'], row['target_id']) == key), None)
    # Synchronizing a revision or actor/time is not a fresh meaning judgment.
    return old is not None and (check['verdict'], check['reason']) == (old['verdict'], old['reason'])


def seal_assessment_reuse(run_dir: Path, *, root: Path) -> dict[str, Any]:
    """Bind supplied fresh judgments; this action does not make those judgments."""
    from . import semantic_workflow as workflow
    template = load_json(run_dir / 'reuse-template.json')
    review = load_json(run_dir / 'semantic-assessment.json')
    workflow.check_schema(review, 'knowledge-semantic-assessment-v1.schema.json')
    sealed = deepcopy(template)
    keyed = {(x['target_kind'], x['target_id']): x for x in review['checks']}
    actor = {k: review[k] for k in ('reviewer','reviewer_type','model','check_method','checked_at')}
    if not actor['reviewer'].strip() or not actor['checked_at'].strip():
        _fail('fresh judgment actor/time must be supplied')
    for row in sealed['targets']:
        key = row['target_kind'], row['target_id']
        if key not in keyed or keyed[key]['verdict'] == 'pending':
            _fail('actual fresh judgment is still pending')
        if row['mode'] == 'fresh-required':
            if (v3.digest(keyed[key]) == row['prior_check_sha256'] or actor['checked_at'] == row['prior_reviewer']['checked_at']
                    or _same_prior_judgment(keyed[key], run_dir, key)):
                _fail('fresh-required target cannot simply carry an old judgment')
            row.update(check_sha256=v3.digest(keyed[key]), reviewer=actor, recheck_rationale=keyed[key]['reason'])
    sealed.update(status='sealed', assessment_sha256=workflow.file_hash(run_dir / 'semantic-assessment.json'))
    verify_assessment_reuse(run_dir, sealed, root=root)
    workflow.write_immutable(run_dir / 'reuse-provenance.json', sealed)
    return sealed


def verify_assessment_reuse(run_dir: Path, provenance: dict[str, Any], *, root: Path) -> VerifiedCertificate:
    from . import semantic_workflow as workflow
    workflow.check_schema(provenance, 'knowledge-semantic-assessment-reuse-v1.schema.json')
    if provenance['status'] != 'sealed':
        _fail('reuse proof is pending actual fresh judgments')
    base = load_json(run_dir / 'base.json')
    accepted = load_json(run_dir / 'prior-accepted.json')
    if accepted != base:
        _fail('prior accepted projection differs from current base')
    certificate = load_json(run_dir / 'dependency-certificate.json')
    verified = verify_certificate(base, certificate, root=root, prior_run=run_dir / 'prior-evidence')
    drafts = load_json(run_dir / 'checked-drafts.json')
    candidate = v3.candidate_preview(base, drafts, root=root)
    plan = plan_delta(base, candidate, verified)
    if plan['mode'] != 'certified_partial' or load_json(run_dir / 'review-plan.json') != plan:
        _fail('current exact delta is not the certified review plan')
    expected_identity = {'base_sha256': v3.digest(base), 'candidate_sha256': semantic_state(candidate),
        'draft_sha256': v3.digest(drafts), 'inventory_sha256': workflow.file_hash(run_dir / 'source-information.json'),
        'discourse_sha256': workflow.file_hash(run_dir / 'discourse-audit.json'),
        'assessment_sha256': workflow.file_hash(run_dir / 'semantic-assessment.json'),
        'certificate_sha256': v3.digest(certificate), 'plan_sha256': plan['plan_sha256'], 'prior': certificate['prior']}
    if any(provenance[k] != value for k, value in expected_identity.items()):
        _fail('current assessment/candidate/input/certificate/plan binding differs')
    prior = verify_prior_run(run_dir / 'prior-evidence', base, root=root)
    if provenance['inventory_sha256'] != prior['inventory_sha256'] or provenance['discourse_sha256'] != prior['discourse_sha256']:
        _fail('source-first inventories cannot be reused after a byte change')
    old_checks, old_targets = _prior_checks(prior), targets(base)
    review = load_json(run_dir / 'semantic-assessment.json')
    actor = {k: review[k] for k in ('reviewer','reviewer_type','model','check_method','checked_at')}
    keyed = {(x['target_kind'], x['target_id']): x for x in review['checks']}
    rows = {(x['target_kind'], x['target_id']): x for x in provenance['targets']}
    planned = {(x['target_kind'], x['target_id']): x for x in plan['targets']}
    if len(rows) != len(provenance['targets']) or len(keyed) != len(review['checks']) or set(rows) != set(planned) or set(keyed) != set(planned):
        _fail('reuse partition/assessment targets must cover exactly once')
    old_actor = {k: prior['review'][k] for k in actor}
    for key, row in rows.items():
        old_check = old_checks[key]
        if any(row[k] != planned[key][k] for k in ('target_kind','target_id','mode','stamp')):
            _fail('affected target mislabeled as reused or stamp differs')
        if row['prior_stamp'] != target_stamp(key[0], old_targets[key]) or row['prior_check_sha256'] != v3.digest(old_check) or row['prior_revision_id'] != base['revision']['id'] or row['prior_review_id'] != old_targets[key].get('review_id') or row['prior_reviewer'] != old_actor:
            _fail('prior target judgment/review identity differs')
        if row['check_sha256'] != v3.digest(keyed[key]):
            _fail('judgment changed after provenance was sealed')
        if row['mode'] == 'reused':
            if keyed[key] != old_check or row['stamp'] != row['prior_stamp'] or row['reviewer'] != old_actor or row['recheck_rationale'] is not None:
                _fail('reused judgment/actor/revision must remain exact')
        elif row['reviewer'] != actor or keyed[key]['verdict'] == 'pending' or v3.digest(keyed[key]) == v3.digest(old_check) or actor['checked_at'] == old_actor['checked_at'] or (keyed[key]['verdict'], keyed[key]['reason']) == (old_check['verdict'], old_check['reason']) or row['recheck_rationale'] != keyed[key]['reason'] or not row['recheck_rationale'].strip():
            _fail('fresh-required target lacks an actual new recorded judgment')
    return verified
