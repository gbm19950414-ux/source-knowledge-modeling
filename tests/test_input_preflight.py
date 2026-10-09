"""Public input checks precede independent semantic judgment without accepting it."""
from copy import deepcopy
import pytest
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import KnowledgeFoundationError, load_json
from source_knowledge_modeling import semantic_v3 as v3, semantic_workflow as workflow
from test_semantic import ROOT
from test_semantic_v3 import _workspace, _write


def _preflight(run, output):
    assert main(['assess', str(run), '--root', str(ROOT), '--inputs-only', '--output', str(output)]) == 0
    return load_json(output)


@pytest.mark.parametrize('slice_name', ['technical', 'research-a', 'research-b'])
def test_source_silence_names_missing_scope_before_independent_review(tmp_path, slice_name):
    run, _ = _workspace(tmp_path / slice_name)
    audit = load_json(run / 'discourse-audit.json')
    audit['items'][0]['status'] = 'not_stated'
    _write(run / 'discourse-audit.json', audit)
    authored = load_json(run / 'drafts.json')
    mapped = authored['proposals'][0]['upsert_discourse'][0]
    mapped.update(status='not_stated', checked_scope=['line:5'])
    _write(run / 'drafts.json', authored)
    before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
    report = _preflight(run, tmp_path / 'input-check.json')
    assert report['status'] == 'invalid'
    failure = next(x for x in report['checks'] if x['id'] == 'discourse:checked-scope:discourse-none')
    assert failure['status'] == 'fail'
    assert all(x in failure['detail'] for x in ('discourse-audit.json', 'discourse-none', 'checked_scope', '实际已读'))
    assert before == {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}


@pytest.mark.parametrize('wrong_field', ['draft_sha256', 'inventory_sha256', 'discourse_sha256'])
def test_completed_assessment_hash_conventions_are_checked_without_rewriting(tmp_path, wrong_field):
    run, _ = _workspace(tmp_path)
    canonical = v3.normalize_drafts(load_json(run / 'base.json'), load_json(run / 'drafts.json'), root=ROOT)
    assessment = load_json(run / 'semantic-assessment.json')
    assessment.update(draft_sha256=v3.digest(canonical), inventory_sha256=workflow.file_hash(run / 'source-information.json'), discourse_sha256=workflow.file_hash(run / 'discourse-audit.json'))
    assessment[wrong_field] = (workflow.file_hash(run / 'drafts.json') if wrong_field == 'draft_sha256'
                               else v3.digest(load_json(run / ('source-information.json' if wrong_field == 'inventory_sha256' else 'discourse-audit.json'))))
    _write(run / 'semantic-assessment.json', assessment)
    saved = (run / 'semantic-assessment.json').read_bytes()
    report = _preflight(run, tmp_path / 'identity.json')
    assert report['status'] == 'invalid'
    failure = next(x for x in report['checks'] if x['id'] == 'assessment:identity')
    assert 'canonical' in failure['detail'] and 'raw' in failure['detail'] and wrong_field in failure['detail']
    assert (run / 'semantic-assessment.json').read_bytes() == saved


def test_inputs_valid_does_not_accept_semantics_or_enter_manifest(tmp_path):
    run, _ = _workspace(tmp_path)
    report = _preflight(run, tmp_path / 'valid.json')
    assert report['schema_version'] == 'knowledge-semantic-input-check/v1'
    assert report['status'] == 'valid' and report['semantic_status'] == 'not_assessed'
    assert report['semantic_review_required'] is True
    assert report['identity']['draft_sha256'] == v3.digest(v3.normalize_drafts(load_json(run / 'base.json'), load_json(run / 'drafts.json'), root=ROOT))
    assert report['identity']['inventory_sha256'] == workflow.file_hash(run / 'source-information.json')
    assert report['identity']['guide_sha256'] == workflow.guide_hash()
    assert load_json(run / 'run-manifest.json')['records'] == []
    assert not (run / 'candidate.json').exists() and not (run / 'checked-drafts.json').exists()
    with pytest.raises(KnowledgeFoundationError):
        workflow.verify_assessment(run, tmp_path / 'valid.json', root=ROOT)


def test_v3_export_missing_explicit_producer_is_reported_before_resolution(tmp_path, capsys):
    run, _ = _workspace(tmp_path)
    assert main(['export', str(run / 'base.json'), '--root', str(tmp_path / 'missing-root'), '--output', str(tmp_path / 'export.json')]) == 2
    assert 'export input: V3 requires explicit --producer-commit' in capsys.readouterr().err
    assert not (tmp_path / 'export.json').exists()


def test_input_preflight_preserves_all_five_unreferenced_object_ids(tmp_path, capsys):
    run, _ = _workspace(tmp_path)
    authored = load_json(run / 'drafts.json')
    proposal = authored['proposals'][0]
    seed = proposal['upsert_elements'][0]
    ids = ['omitted-a', 'omitted-b', 'omitted-c', 'omitted-d', 'omitted-e']
    proposal['upsert_elements'] += [dict(deepcopy(seed), id=ident) for ident in ids]
    _write(run / 'drafts.json', authored)
    assert main(['assess', str(run), '--root', str(ROOT), '--inputs-only', '--output', str(tmp_path / 'package.json')]) == 2
    error = capsys.readouterr().err
    assert all(ident in error for ident in ids)
    assert not (run / 'candidate.json').exists()


@pytest.mark.parametrize('mismatch', [False, True])
def test_model_silence_scope_missing_or_different_is_never_repaired(tmp_path, mismatch, capsys):
    run, _ = _workspace(tmp_path)
    audit = load_json(run / 'discourse-audit.json')
    audit['items'][0].update(status='not_stated', checked_scope=['line:5'])
    _write(run / 'discourse-audit.json', audit)
    authored = load_json(run / 'drafts.json')
    authored['proposals'][0]['upsert_discourse'][0]['status'] = 'not_stated'
    if mismatch:
        # Empty differs and also fails the existing normalization scope gate.
        authored['proposals'][0]['upsert_discourse'][0]['checked_scope'] = []
    _write(run / 'drafts.json', authored)
    saved = (run / 'drafts.json').read_bytes()
    assert main(['assess', str(run), '--root', str(ROOT), '--inputs-only', '--output', str(tmp_path / 'scope.json')]) == 2
    error = capsys.readouterr().err
    assert all(x in error for x in ('drafts.json', 'discourse-none', 'checked_scope', '实际已读'))
    assert (run / 'drafts.json').read_bytes() == saved


@pytest.mark.parametrize('command', ['review', 'publish'])
def test_public_acceptance_rejects_valid_input_report(tmp_path, command, capsys):
    run, _ = _workspace(tmp_path)
    output = tmp_path / 'input.json'
    assert _preflight(run, output)['status'] == 'valid'
    flags = (['--proposal-id', 'p-amount', '--decision', 'accept', '--reviewer', 'synthetic']
             if command == 'review' else ['--revision-id', 'published'])
    assert main([command, str(run / 'base.json'), '--root', str(ROOT), '--drafts', str(run / 'checked-drafts.json'),
                 '--source-checks', str(run / 'absent-checks.json'), '--ledger', str(run / 'absent-ledger.json'),
                 '--assessment', str(output), '--run-dir', str(run), '--output', str(tmp_path / 'accepted.json'), *flags]) == 2
    assert 'knowledge-semantic-quality' in capsys.readouterr().err
    assert load_json(run / 'run-manifest.json')['records'] == []


def test_runbook_preflight_order_and_distinct_reports(tmp_path):
    run, _ = _workspace(tmp_path)
    book = (run / 'RUNBOOK.md').read_text()
    assert book.index('input-check-before-review.json') < book.index('skm draft-import')
    assert book.index('input-check-after-review.json') < book.index('--output ' + str(run / 'quality-report.json'))
    assert '不是语义 pass' in book


def test_valid_identity_does_not_turn_failed_recorded_judgment_into_pass(tmp_path):
    run, _ = _workspace(tmp_path)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(ROOT), '--drafts', str(run / 'drafts.json'),
                 '--run-dir', str(run), '--output', str(run / 'checked-drafts.json')]) == 0
    drafts = load_json(run / 'checked-drafts.json')
    assessment = load_json(run / 'semantic-assessment.json')
    assessment.update(draft_sha256=v3.digest(drafts), inventory_sha256=workflow.file_hash(run / 'source-information.json'),
                      discourse_sha256=workflow.file_hash(run / 'discourse-audit.json'), reviewer='synthetic actual reviewer',
                      checked_at='2026-10-05T00:00:00Z', checks=[{'target_kind': 'element', 'target_id': 'no-amount',
                      'verdict': 'fail', 'reason': 'Synthetic recorded failure stays a failure.',
                      'content_revision': drafts['proposals'][0]['upsert_elements'][0]['content_revision']}])
    _write(run / 'semantic-assessment.json', assessment)
    saved = (run / 'semantic-assessment.json').read_bytes()
    assert _preflight(run, tmp_path / 'inputs-valid.json')['status'] == 'valid'
    assert workflow.assess_workspace(run, root=ROOT)['status'] == 'fail'
    assert (run / 'semantic-assessment.json').read_bytes() == saved


def test_source_scope_different_from_actual_model_scope_reports_both_files(tmp_path):
    run, _ = _workspace(tmp_path)
    audit = load_json(run / 'discourse-audit.json')
    audit['items'][0].update(status='not_stated', checked_scope=['line:4', 'line:5'])
    _write(run / 'discourse-audit.json', audit)
    authored = load_json(run / 'drafts.json')
    authored['proposals'][0]['upsert_discourse'][0].update(status='not_stated', checked_scope=['line:5'])
    _write(run / 'drafts.json', authored)
    report = _preflight(run, tmp_path / 'different-scope.json')
    failure = next(x for x in report['checks'] if x['id'] == 'discourse:checked-scope:discourse-none')
    assert report['status'] == 'invalid' and failure['status'] == 'fail'
    assert 'discourse-audit.json and drafts.json' in failure['detail']


def test_preflight_rejects_modified_disk_candidate_without_repair(tmp_path):
    run, _ = _workspace(tmp_path)
    assert main(['draft-import', str(run / 'base.json'), '--root', str(ROOT), '--drafts', str(run / 'drafts.json'),
                 '--run-dir', str(run), '--output', str(run / 'checked-drafts.json')]) == 0
    initial = _preflight(run, tmp_path / 'before-tamper.json')
    assert initial['status'] == 'valid'
    candidate = load_json(run / 'candidate.json')
    candidate['elements'][0]['text'] = 'Tampered assertion outside the frozen authored input.'
    _write(run / 'candidate.json', candidate)
    saved = {name: (run / name).read_bytes() for name in ('drafts.json', 'checked-drafts.json', 'candidate.json',
             'semantic-assessment.json', 'run-manifest.json')}
    report = _preflight(run, tmp_path / 'after-tamper.json')
    assert report['status'] == 'invalid' and report['semantic_review_required'] is True
    failure = next(x for x in report['checks'] if x['id'] == 'candidate:identity')
    assert failure['status'] == 'fail' and 'candidate.json' in failure['detail']
    assert saved == {name: (run / name).read_bytes() for name in saved}
