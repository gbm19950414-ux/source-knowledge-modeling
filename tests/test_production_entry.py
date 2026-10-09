"""Synthetic public-entry checks; no reference or production document inputs."""
import json
from pathlib import Path
import shlex

import pytest

from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import load_json
from source_knowledge_modeling.ingest import TextAdapter
from source_knowledge_modeling import semantic_workflow as workflow


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _inputs(tmp_path):
    source = tmp_path / 'team note.txt'
    source.write_text('The room opens at nine.\nVisitors use the east door.\n', encoding='utf-8')
    scope = tmp_path / 'scope.json'
    _write(scope, {'description': 'Synthetic room opening; visitor line is context',
                   'included_locators': ['line:1'], 'context_locators': ['line:2']})
    run = tmp_path / 'production run'
    return source, scope, run, ['start', str(source), '--root', str(tmp_path), '--output-dir', str(run),
                               '--scope', str(scope), '--profile', 'instructional-source@3.0.0']


def test_start_reuses_extraction_and_preserves_authored_inputs(tmp_path, monkeypatch, capsys):
    source, scope, run, command = _inputs(tmp_path)
    original = TextAdapter.extract
    calls = []
    def counted(self, *args, **kwargs):
        calls.append(1)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(TextAdapter, 'extract', counted)
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert not first['ingest_reused'] and not first['reused']
    assert first['tokens'] == first['model'] == 'unknown'
    assert first['dependency_policy'] == 'full_scope'
    assert first['scope'] == load_json(scope)
    assert load_json(run / 'base.json')['reviews'] == []
    assert [u['scope_role'] for u in load_json(run / 'work-package.json')['source_units']] == ['primary', 'context']
    authored = {}
    for name in ('drafts.json', 'source-information.json', 'discourse-audit.json', 'semantic-assessment.json'):
        value = load_json(run / name)
        value['author-preservation-marker'] = 'actual author text retained'
        _write(run / name, value)
        authored[name] = (run / name).read_bytes()
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['ingest_reused'] and second['reused']
    assert first['artifact'] == second['artifact']
    assert len(calls) == 1
    assert len(list((run / 'source-inventory').rglob('transcription.json'))) == 1
    assert all((run / name).read_bytes() == content for name, content in authored.items())


@pytest.mark.parametrize('changed', ['source', 'scope', 'foundation', 'snapshot'])
def test_start_rejects_changed_identity_without_touching_authors(tmp_path, changed, capsys):
    source, scope, run, command = _inputs(tmp_path)
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    draft = (run / 'drafts.json').read_bytes()
    path = {'source': source, 'scope': scope, 'foundation': Path(first['artifact']),
            'snapshot': Path(first['snapshot'])}[changed]
    path.write_bytes(path.read_bytes() + b'\n')
    assert main(command) == 2
    assert 'changed' in capsys.readouterr().err
    assert (run / 'drafts.json').read_bytes() == draft
    assert len(list((run / 'source-inventory').rglob('transcription.json'))) == 1
    if changed == 'source':
        fresh = command.copy()
        fresh[fresh.index('--output-dir') + 1] = str(tmp_path / 'fresh run')
        assert main(fresh) == 0
        assert not json.loads(capsys.readouterr().out)['ingest_reused']


def test_runbook_draft_import_is_executable_and_candidate_is_separate(tmp_path):
    source, scope, run, command = _inputs(tmp_path)
    assert main(command) == 0
    base = load_json(run / 'base.json')
    _write(run / 'drafts.json', {'schema_version': 'knowledge-semantic-drafts/v3',
                               'artifact_id': base['artifact_id'], 'base_revision_id': base['revision']['id'],
                               'proposals': [{'id': 'synthetic-opening', 'reason': 'Synthetic assertion from own text',
                                              'upsert_elements': [{'id': 'opening', 'kind': 'assertion',
                                                                   'text': 'The room opens at nine.',
                                                                   'origin_type': 'source_claim', 'modality': 'asserted',
                                                                   'anchors': [{'locator': 'line:1', 'quote': 'The room opens at nine.'}]}],
                                              'upsert_packages': [{'id': 'opening-package', 'summary': 'Room opening',
                                                                   'origin_type': 'source_claim', 'modality': 'asserted',
                                                                   'element_ids': ['opening'], 'relation_ids': [],
                                                                   'context_ids': [], 'legacy_review': None}]}]})
    book = (run / 'RUNBOOK.md').read_text()
    line = next(line for line in book.splitlines() if line.startswith('skm draft-import '))
    args = shlex.split(line)
    assert args[args.index('--drafts') + 1] == str(run / 'drafts.json')
    assert args[args.index('--output') + 1] == str(run / 'checked-drafts.json')
    assert main(args[1:]) == 0
    assert load_json(run / 'checked-drafts.json')['schema_version'] == 'knowledge-semantic-drafts/v3'
    candidate = load_json(run / 'candidate.json')
    assert candidate['schema_version'] == 'knowledge-semantic-projection/v3'
    assert candidate['reviews'] == []
    assert candidate['elements'][0]['review_state'] == 'pending_semantic'
    assert all(t['status'] == 'pending' for t in load_json(run / 'review-targets.json')['targets'])
    assert 'legacy_review' in book and 'context_locators' in book
    assert str(workflow.RESOURCES / 'schemas' / 'knowledge-semantic-drafts-v3.schema.json') in book


def test_start_prepare_failure_retry_reuses_saved_ingest(tmp_path, monkeypatch):
    source, scope, run, command = _inputs(tmp_path)
    original = workflow.prepare_workspace
    def fail_once(*args, **kwargs):
        raise OSError('synthetic prepare interruption')
    monkeypatch.setattr(workflow, 'prepare_workspace', fail_once)
    assert main(command) == 2
    assert (run / 'source-entry.json').is_file()
    monkeypatch.setattr(workflow, 'prepare_workspace', original)
    def forbidden(*args, **kwargs):
        raise AssertionError('ingest must not extract again')
    monkeypatch.setattr(TextAdapter, 'extract', forbidden)
    assert main(command) == 0


def test_start_missing_scope_and_pdf_without_local_parser_remain_blocked(tmp_path, capsys):
    source, scope, run, command = _inputs(tmp_path)
    without_scope = command.copy()
    pos = without_scope.index('--scope')
    del without_scope[pos:pos + 2]
    with pytest.raises(SystemExit) as exc:
        main(without_scope)
    assert exc.value.code == 2
    assert not run.exists()
    pdf = tmp_path / 'scan.pdf'
    pdf.write_bytes(b'synthetic PDF identity; never parsed')
    command[1] = str(pdf)
    command += ['--pages', '1']
    with pytest.raises(SystemExit) as exc:
        main(command)
    assert exc.value.code == 2
    assert 'Active PDF parsing needs --mineru-cli and --mineru-home' in capsys.readouterr().err
    assert not run.exists()


def test_start_existing_local_mineru_json_uses_original_adapter_contract(tmp_path, capsys, monkeypatch):
    from hashlib import sha256
    from source_knowledge_modeling.ingest import MineruPdfAdapter
    pdf = tmp_path / 'scan.pdf'
    pdf.write_bytes(b'Synthetic page source identity for existing-middle adapter')
    middle = tmp_path / 'middle.json'
    _write(middle, {'metadata': {'producer': {'name': 'mineru', 'version': 'synthetic'}},
                    'pages': [{'page_idx': 0, 'blocks': [{'index': 0, 'type': 'text',
                                                        'bbox': [0, 0, 1, 1],
                                                        'content': [{'content': 'The room opens at nine.'}]}]}]})
    scope = tmp_path / 'scope.json'
    _write(scope, {'description': 'Synthetic PDF adapter compatibility',
                   'included_locators': ['pdf-page:1/block:0'], 'context_locators': []})
    run = tmp_path / 'pdf run'
    command = ['start', str(pdf), '--root', str(tmp_path), '--output-dir', str(run),
               '--scope', str(scope), '--profile', 'instructional-source@3.0.0', '--pages', '1',
               '--middle-json', str(middle), '--middle-sha256', sha256(middle.read_bytes()).hexdigest(),
               '--middle-source-sha256', sha256(pdf.read_bytes()).hexdigest(), '--middle-tier', 'standard']
    assert main(command) == 0
    assert load_json(run / 'base.json')['source']['kind'] == 'pdf'
    capsys.readouterr()
    def forbidden(*args, **kwargs):
        raise AssertionError('MinerU extraction must not repeat')
    monkeypatch.setattr(MineruPdfAdapter, 'extract', forbidden)
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)['ingest_reused']
    middle.write_bytes(middle.read_bytes() + b'\n')
    assert main(command) == 2
    assert 'changed' in capsys.readouterr().err


def test_start_rejects_unmanaged_directory_and_inputs_changed_during_ingest(tmp_path, monkeypatch, capsys):
    source, scope, run, command = _inputs(tmp_path)
    run.mkdir()
    (run / 'author.txt').write_text('Keep actual author content')
    assert main(command) == 2
    assert 'empty directory' in capsys.readouterr().err
    assert (run / 'author.txt').read_text() == 'Keep actual author content'
    command[command.index('--output-dir') + 1] = str(tmp_path / 'other run')
    original = TextAdapter.extract
    def changed_during_extract(self, *args, **kwargs):
        extracted = original(self, *args, **kwargs)
        scope.write_bytes(scope.read_bytes() + b'\n')
        return extracted
    monkeypatch.setattr(TextAdapter, 'extract', changed_during_extract)
    assert main(command) == 2
    assert 'scope changed during ingest' in capsys.readouterr().err
    assert not (tmp_path / 'other run' / 'source-entry.json').exists()


def test_start_recovers_runbook_failure_after_manifest_without_reextracting(tmp_path, monkeypatch, capsys):
    source, scope, run, command = _inputs(tmp_path)
    original = workflow.write_runbook
    def interrupted(*args, **kwargs):
        raise OSError('synthetic RUNBOOK write failure after manifest')
    monkeypatch.setattr(workflow, 'write_runbook', interrupted)
    assert main(command) == 2
    assert 'RUNBOOK write failure' in capsys.readouterr().err
    assert (run / 'run-manifest.json').is_file()
    assert not (run / 'RUNBOOK.md').exists()
    preserved = {}
    for name in ('drafts.json', 'source-information.json', 'discourse-audit.json', 'semantic-assessment.json'):
        value = load_json(run / name)
        value['author-preservation-marker'] = 'written while recovering'
        _write(run / name, value)
        preserved[name] = (run / name).read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('recovery must not extract again')
    monkeypatch.setattr(TextAdapter, 'extract', forbidden)
    # A continuing RUNBOOK failure must remain visible, never report success.
    assert main(command) == 2
    assert not (run / 'RUNBOOK.md').exists()
    capsys.readouterr()
    monkeypatch.setattr(workflow, 'write_runbook', original)
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['ingest_reused'] and result['reused']
    assert (run / 'RUNBOOK.md').is_file()
    assert all((run / name).read_bytes() == value for name, value in preserved.items())
    assert len(list((run / 'source-inventory').rglob('transcription.json'))) == 1


@pytest.mark.parametrize('flag,value', [('--adapter', 'mineru'), ('--pages', '1'),
                                       ('--mineru-home', 'new-local-home'),
                                       ('--middle-json', 'new-middle.json'),
                                       ('--order-overrides', 'new-order.json'),
                                       ('--profile', 'instructional-source@2.0.0')])
def test_start_rejects_other_identity_changes_before_reextracting(tmp_path, flag, value, capsys, monkeypatch):
    source, scope, run, command = _inputs(tmp_path)
    assert main(command) == 0
    capsys.readouterr()
    author_bytes = (run / 'drafts.json').read_bytes()
    if flag in ('--middle-json', '--order-overrides'):
        path = tmp_path / value
        _write(path, {'synthetic': True})
        value = str(path)
    if flag == '--adapter':
        # An explicit middle file makes MinerU constructible without a local
        # server; identity rejection must happen before calling its extractor.
        middle = tmp_path / 'adapter-middle.json'
        _write(middle, {'synthetic': True})
        command += ['--middle-json', str(middle)]
    if flag == '--profile':
        command[command.index(flag)+1] = value
    else:
        command += [flag, value]
    def forbidden(*args, **kwargs):
        raise AssertionError('identity changes must not reach extraction')
    monkeypatch.setattr(TextAdapter, 'extract', forbidden)
    from source_knowledge_modeling.ingest import MineruPdfAdapter
    monkeypatch.setattr(MineruPdfAdapter, 'extract', forbidden)
    assert main(command) == 2
    assert (run / 'drafts.json').read_bytes() == author_bytes
    assert len(list((run / 'source-inventory').rglob('transcription.json'))) == 1
    assert capsys.readouterr().err


def test_start_snapshot_index_must_match_foundation_snapshot(tmp_path, capsys, monkeypatch):
    source, scope, run, command = _inputs(tmp_path)
    assert main(command) == 0
    capsys.readouterr()
    entry = load_json(run / 'source-entry.json')
    author_bytes = (run / 'drafts.json').read_bytes()
    # Both file and hash are individually valid, but this is not the snapshot
    # actually bound by the canonical frozen foundation.
    entry['snapshot'] = {'path': 'drafts.json', 'sha256': workflow.file_hash(run / 'drafts.json')}
    _write(run / 'source-entry.json', entry)
    def forbidden(*args, **kwargs):
        raise AssertionError('tampered index must not extract again')
    monkeypatch.setattr(TextAdapter, 'extract', forbidden)
    assert main(command) == 2
    assert 'snapshot identity differs' in capsys.readouterr().err
    assert (run / 'drafts.json').read_bytes() == author_bytes
