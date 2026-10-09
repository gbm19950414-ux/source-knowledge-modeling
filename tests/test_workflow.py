from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from source_knowledge_modeling.core import KnowledgeFoundationError, coverage, impact, load_json, query, render_markdown, validate
from source_knowledge_modeling.export import export_manifest
from source_knowledge_modeling.ingest import MineruPdfAdapter, TextAdapter, apply_reading_order, ingest, units_from_mineru_middle
from source_knowledge_modeling.review import new_review_ledger, publish, record_decision, validate_drafts

ROOT = Path(__file__).resolve().parents[1]
GARDEN = ROOT / 'contracts/knowledge-foundation/v1/examples/community-garden.v1.json'


def test_unmodified_v1_fixture_and_views():
    artifact = load_json(GARDEN)
    counts = validate(artifact, root=ROOT, resolve=True)
    assert counts['unit_count'] == 5
    assert query(artifact, term='浇水')['annotations']
    assert '```mermaid' in render_markdown(artifact)
    assert export_manifest(artifact, root=ROOT, producer_commit='test-commit')['source']['sha256'] == artifact['source']['sha256']


def test_text_ingest_and_source_integrity(tmp_path):
    source = tmp_path / 'input.txt'
    source.write_text('Title\n\nThe steps depend on rain.\n', encoding='utf-8')
    path, snapshot = ingest(source, root=tmp_path, workspace=tmp_path / 'runs', adapter=TextAdapter())
    artifact = load_json(path)
    assert validate(artifact, root=tmp_path, resolve=True)['unit_count'] == 3
    assert artifact['units'][1]['kind'] == 'blank'
    assert all(unit['interpretation_status'] in ('unreviewed', 'not_applicable') for unit in artifact['units'])
    assert query(artifact)['unreviewed_locators'] == ['line:1', 'line:3']
    missing_locator = deepcopy(artifact)
    missing_locator['units'][0]['locator'] = 'line:99'
    with pytest.raises(KnowledgeFoundationError, match='locator is absent'):
        validate(missing_locator, root=tmp_path, resolve=True)
    snapshot.write_text(snapshot.read_text(encoding='utf-8').replace('rain', 'sun'), encoding='utf-8')
    with pytest.raises(KnowledgeFoundationError, match='revision mismatch'):
        validate(artifact, root=tmp_path, resolve=True)
    source.unlink()
    with pytest.raises(KnowledgeFoundationError, match='unavailable'):
        validate(artifact, root=tmp_path, resolve=True)


def test_draft_review_publish_lineage(tmp_path):
    source = tmp_path / 'input.txt'
    source.write_text('On Saturday, inspect the bed.\nIf dry, water it.\n', encoding='utf-8')
    path, _ = ingest(source, root=tmp_path, workspace=tmp_path / 'runs', adapter=TextAdapter())
    base = load_json(path)
    a1 = {'id': 'a-inspect', 'origin_type': 'source_claim', 'role': 'step', 'summary': 'Inspect on Saturday',
          'unit_ids': ['u1'], 'conditions': ['Saturday'], 'exceptions': [], 'source_checked': False, 'scientific_status': 'not_applicable'}
    a2 = {'id': 'a-water', 'origin_type': 'source_claim', 'role': 'conditional_action', 'summary': 'Water if dry',
          'unit_ids': ['u2'], 'conditions': ['dry'], 'exceptions': [], 'source_checked': False, 'scientific_status': 'not_applicable'}
    rel = {'id': 'r-order', 'type': 'precedes', 'from_id': 'a-inspect', 'to_id': 'a-water',
           'support_unit_ids': ['u1', 'u2'], 'origin_type': 'project_interpretation', 'source_checked': False, 'note': 'Order in text'}
    drafts = {'schema_version': 'knowledge-drafts/v1', 'artifact_id': base['artifact_id'], 'base_revision_id': base['revision']['id'],
              'proposals': [{'id': 'p1', 'kind': 'annotation', 'value': a1}, {'id': 'p2', 'kind': 'annotation', 'value': a2},
                            {'id': 'p3', 'kind': 'relation', 'value': rel},
                            {'id': 'p4', 'kind': 'annotation', 'value': {**a2, 'id': 'a-rejected'}},
                            {'id': 'p5', 'kind': 'annotation', 'value': {**a1, 'id': 'a-pending'}}]}
    validate_drafts(base, drafts)
    ledger = new_review_ledger(drafts)
    with pytest.raises(KnowledgeFoundationError, match='cite every'):
        record_decision(base, drafts, ledger, proposal_id='p1', decision='accept', reviewer='R', checked_locators=[], note='')
    for pid, locators in [('p1', ['line:1']), ('p2', ['line:2']), ('p3', ['line:1', 'line:2'])]:
        ledger = record_decision(base, drafts, ledger, proposal_id=pid, decision='accept', reviewer='R', checked_locators=locators, note='read original')
    ledger = record_decision(base, drafts, ledger, proposal_id='p4', decision='reject', reviewer='R', checked_locators=['line:2'], note='incorrect claim')
    ledger = record_decision(base, drafts, ledger, proposal_id='p5', decision='pending', reviewer='R', checked_locators=[], note='need more context')
    changed_after_review = deepcopy(drafts)
    changed_after_review['proposals'][0]['value']['summary'] = 'An unreviewed replacement statement'
    with pytest.raises(KnowledgeFoundationError, match='changed since review'):
        publish(base, changed_after_review, ledger, revision_id='r2-tampered', root=tmp_path)
    later = publish(base, drafts, ledger, revision_id='r2-reviewed', root=tmp_path)
    assert base['annotations'] == []
    assert len(later['annotations']) == 2 and len(later['relations']) == 1
    assert {item['id'] for item in later['annotations']} == {'a-inspect', 'a-water'}
    assert query(later, term='water')['annotations'][0]['locators'] == ['line:2']
    assert query(later, term='water')['annotations'][0]['review']['reviewer'] == 'R'
    assert impact(base, later)['affected_annotation_ids'] == ['a-inspect', 'a-water']
    assert validate(later, root=tmp_path, resolve=True)['interpretation'] == {'partially_interpreted': 2}
    manifest = export_manifest(later, root=tmp_path, producer_commit='abc')
    assert manifest['annotations'][0]['source_anchors'] == ['line:1']
    assert manifest['annotations'][0]['review']['proposal_sha256'] == later['annotations'][0]['review']['proposal_sha256']
    assert manifest['artifact_revision_id'] == 'r2-reviewed'


def test_pdf_middle_inventory_keeps_visual_gap():
    middle = {'pages': [{'page_idx': 0, 'blocks': [
        {'index': 0, 'type': 'text', 'bbox': [0, 0, 1, 0.5], 'content': [{'content': 'Paragraph'}]},
        {'index': 1, 'type': 'image', 'bbox': [0, 0.5, 1, 1], 'content': []},
    ]}]}
    units = units_from_mineru_middle(middle)
    assert [u.locator for u in units] == ['pdf-page:1/block:0', 'pdf-page:1/block:1']
    assert units[1].visual_ref == 'pdf-page:1'
    with pytest.raises(KnowledgeFoundationError, match='Duplicate'):
        units_from_mineru_middle({'pages': [middle['pages'][0], middle['pages'][0]]})


def test_local_mineru_reuse_retains_empty_page_raw_visual_and_exact_range(tmp_path):
    from source_knowledge_modeling.core import digest_bytes
    source = tmp_path / 'scan.pdf'
    source.write_bytes(b'local scanned source fixture')
    middle = {'metadata': {'producer': {'name': 'mineru', 'version': '4.0.5'}}, 'pages': [
        {'page_idx': 0, 'blocks': [{'index': 0, 'type': 'text', 'bbox': [0,0,1,1], 'content': [{'content': 'outside'}]}]},
        {'page_idx': 1, 'blocks': [{'index': 0, 'type': 'chart', 'bbox': [0,0,1,1],
                                    'content': [{'type': 'chart_body', 'content': ''}, {'type': 'chart_caption', 'content': [{'content': 'Caption'}]}]}]},
        {'page_idx': 2},
    ]}
    middle_path = tmp_path / 'existing-basic.json'
    middle_path.write_text(json.dumps(middle), encoding='utf-8')
    source_hash = digest_bytes(source.read_bytes())
    middle_hash = digest_bytes(middle_path.read_bytes())
    adapter = MineruPdfAdapter(middle_json=middle_path,
                               middle_sha256=middle_hash, middle_source_sha256=source_hash, middle_tier='basic')
    units, parser = adapter.extract(source, pages='2-3')
    assert [u.locator for u in units] == ['pdf-page:2/block:0', 'pdf-page:3/empty']
    assert units[0].text == 'Caption' and units[0].visual_ref == 'pdf-page:2'
    assert units[0].raw_content == middle['pages'][1]['blocks'][0]['content']
    assert parser['tier'] == 'basic' and parser['input_sha256'] == middle_hash
    path, snapshot_path = ingest(source, root=tmp_path, workspace=tmp_path / 'runs', adapter=adapter, pages='2-3')
    artifact = load_json(path)
    snapshot = load_json(snapshot_path)
    assert validate(artifact, root=tmp_path, resolve=True)['unit_count'] == 2
    assert snapshot['units'][1]['kind'] == 'page_empty'
    assert len(artifact['scope']['included_locators']) == 2
    with pytest.raises(KnowledgeFoundationError, match='cover every requested'):
        adapter.extract(source, pages='2-4')
    wrong = MineruPdfAdapter(executable=tmp_path / 'no', home=tmp_path / 'unused', middle_json=middle_path,
                             middle_sha256='0'*64, middle_source_sha256=source_hash, middle_tier='basic')
    with pytest.raises(KnowledgeFoundationError, match='hash mismatch'):
        wrong.extract(source, pages='2-3')


def test_reading_order_correction_and_pdf_visual_review_gate(tmp_path):
    from source_knowledge_modeling.core import digest_bytes
    source = tmp_path / 'scan.pdf'
    source.write_bytes(b'page with crossed OCR order')
    middle = {'metadata': {'producer': {'name': 'mineru', 'version': '4.0.5'}}, 'pages': [
        {'page_idx': 0, 'blocks': [
            {'index': 0, 'type': 'text', 'bbox': [0,0,1,.5], 'content': [{'content': 'second'}]},
            {'index': 1, 'type': 'text', 'bbox': [0,.5,1,1], 'content': [{'content': 'first'}]},
        ]}]}
    middle_path = tmp_path / 'middle.json'
    middle_path.write_text(json.dumps(middle), encoding='utf-8')
    adapter = MineruPdfAdapter(executable=tmp_path / 'no', home=tmp_path / 'unused', middle_json=middle_path,
                               middle_sha256=digest_bytes(middle_path.read_bytes()),
                               middle_source_sha256=digest_bytes(source.read_bytes()), middle_tier='basic')
    extracted, _ = adapter.extract(source, pages='1')
    override = {'schema_version': 'knowledge-reading-order/v1',
                'pages': {'1': ['pdf-page:1/block:1', 'pdf-page:1/block:0']},
                'continuations': {'pdf-page:1/block:0': 'pdf-page:1/block:1'}}
    assert [u.text for u in apply_reading_order(extracted, override)] == ['first', 'second']
    with pytest.raises(KnowledgeFoundationError, match='every unit exactly once'):
        apply_reading_order(extracted, {**override, 'pages': {'1': ['pdf-page:1/block:1']}})
    override_path = tmp_path / 'order.json'
    override_path.write_text(json.dumps(override), encoding='utf-8')
    path, snapshot_path = ingest(source, root=tmp_path, workspace=tmp_path / 'runs', adapter=adapter,
                                 pages='1', order_overrides=override_path)
    base = load_json(path)
    assert [u['locator'] for u in base['units']] == override['pages']['1']
    original_path, _ = ingest(source, root=tmp_path, workspace=tmp_path / 'runs', adapter=adapter, pages='1')
    original = load_json(original_path)
    assert {u['locator']:u['id'] for u in original['units']} == {u['locator']:u['id'] for u in base['units']}
    assert load_json(snapshot_path)['parser']['reading_order_sha256'] == digest_bytes(override_path.read_bytes())
    assert load_json(snapshot_path)['units'][1]['continues_from'] == 'pdf-page:1/block:1'
    proposal = {'id':'p1','kind':'annotation','value': {'id':'a1','origin_type':'source_example','role':'example',
                'summary':'first','unit_ids':[base['units'][0]['id']],'conditions':[],'exceptions':[],
                'source_checked':False,'scientific_status':'not_assessed','applicability_status':'not_assessed'}}
    drafts = {'schema_version':'knowledge-drafts/v1','artifact_id':base['artifact_id'],
              'base_revision_id':base['revision']['id'],'proposals':[proposal]}
    ledger = new_review_ledger(drafts)
    with pytest.raises(KnowledgeFoundationError, match='original visual'):
        record_decision(base,drafts,ledger,proposal_id='p1',decision='accept',reviewer='AI',
                        checked_locators=['pdf-page:1/block:1'],note='parsed only',check_method='parsed_text')
    ledger = record_decision(base,drafts,ledger,proposal_id='p1',decision='accept',reviewer='AI',
                             checked_locators=['pdf-page:1/block:1'],note='viewed original',check_method='original_visual')
    published = publish(base,drafts,ledger,revision_id='r2',root=tmp_path)
    assert query(published)['annotations'][0]['origin_type'] == 'source_example'
    assert query(published)['annotations'][0]['review']['check_method'] == 'original_visual'
    assert '适用性评估：not_assessed' in render_markdown(published)


def test_cli_review_publish_query_and_export(tmp_path, capsys):
    from source_knowledge_modeling.cli import main
    source = tmp_path / 'source.txt'
    source.write_text('Inspect on Saturday.\n', encoding='utf-8')
    assert main(['ingest', str(source), '--root', str(tmp_path), '--workspace', str(tmp_path / 'runs')]) == 0
    base_path = next((tmp_path / 'runs').rglob('foundation.json'))
    base = load_json(base_path)
    proposal = {'schema_version': 'knowledge-drafts/v1', 'artifact_id': base['artifact_id'],
                'base_revision_id': base['revision']['id'], 'proposals': [{
                    'id': 'p1', 'kind': 'annotation', 'value': {
                        'id': 'a1', 'origin_type': 'source_claim', 'role': 'step', 'summary': 'Inspect on Saturday',
                        'unit_ids': ['u1'], 'conditions': ['Saturday'], 'exceptions': [],
                        'source_checked': False, 'scientific_status': 'not_applicable'}}]}
    incoming = tmp_path / 'incoming.json'
    incoming.write_text(json.dumps(proposal), encoding='utf-8')
    drafts = tmp_path / 'drafts.json'
    ledger = tmp_path / 'ledger.json'
    published = tmp_path / 'published.json'
    view = tmp_path / 'view.md'
    exported = tmp_path / 'export.json'
    assert main(['draft-import', str(base_path), '--root', str(tmp_path), '--drafts', str(incoming), '--output', str(drafts)]) == 0
    assert main(['review', str(base_path), '--root', str(tmp_path), '--drafts', str(drafts), '--ledger', str(ledger),
                 '--proposal-id', 'p1', '--decision', 'accept', '--reviewer', 'human', '--checked-locator', 'line:1',
                 '--output', str(ledger)]) == 0
    assert main(['publish', str(base_path), '--root', str(tmp_path), '--drafts', str(drafts), '--ledger', str(ledger),
                 '--revision-id', 'r2', '--output', str(published)]) == 0
    assert main(['query', str(published), '--term', 'Saturday']) == 0
    assert main(['render', str(published), '--output', str(view)]) == 0
    assert 'line:1' in view.read_text(encoding='utf-8')
    assert main(['export', str(published), '--root', str(tmp_path), '--producer-commit', 'test-commit',
                 '--output', str(exported)]) == 0
    assert load_json(exported)['annotations'][0]['source_anchors'] == ['line:1']
    assert main(['publish', str(base_path), '--root', str(tmp_path), '--drafts', str(drafts), '--ledger', str(ledger),
                 '--revision-id', 'r2', '--output', str(published)]) == 2  # immutable output
