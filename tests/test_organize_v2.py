"""Synthetic non-training contracts for explicit reading structures."""
from copy import deepcopy
from pathlib import Path

import pytest

from source_knowledge_modeling import organize as o
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import load_json

FIXTURE = Path(__file__).resolve().parents[1] / 'contracts/semantic-discourse/v1/examples'
EXPORT = FIXTURE / 'garden.export.json'


def draft_v2():
    draft = load_json(FIXTURE / 'garden.draft.json')
    draft['schema_version'] = 'semantic-discourse-draft/v2'
    def ref(element):
        return {'object_id': element, 'field_path': '/text'}
    draft['reading_structures'] = [
        {'id': 'structure:chapter', 'scope_id': 'garden', 'pattern': 'comparison',
         'core_ids': ['t-water', 't-limits'], 'supports': [], 'independent_ids': [],
         'reading_order': ['t-water', 't-limits'], 'refs': [ref('check'), ref('unknown-volume')],
         'rationale': 'Compare the action with the explicitly absent volume.'},
        {'id': 'structure:water', 'scope_id': 't-water', 'pattern': 'sequence',
         'core_ids': ['b-check', 'b-water'],
         'supports': [{'node_id': 'b-condition', 'anchor_id': 'b-water', 'role': 'condition'}],
         'independent_ids': [], 'reading_order': ['b-check', 'b-condition', 'b-water'],
         'refs': [ref('check'), ref('dry'), ref('water')],
         'rationale': 'The condition is attached to watering, without creating an action edge.'},
        {'id': 'structure:limits', 'scope_id': 't-limits', 'pattern': 'explanation',
         'core_ids': ['b-limits'], 'supports': [], 'independent_ids': [],
         'reading_order': ['b-limits'], 'refs': [ref('unknown-volume')],
         'rationale': 'Keep the absent volume explicit.'},
        {'id': 'structure:block', 'scope_id': 'b-water', 'pattern': 'multiple',
         'core_ids': [], 'supports': [], 'independent_ids': ['water', 'dry', 'unknown-volume'],
         'reading_order': ['dry', 'water', 'unknown-volume'], 'refs': [ref('water')],
         'rationale': 'Keep these related components independent.'},
    ]
    return draft


def run_v2(tmp_path, draft=None):
    run = tmp_path / 'v2'
    assert main(['organize', 'prepare', str(EXPORT), '--output-dir', str(run), '--format-version', '2']) == 0
    assert load_json(run / 'draft.json')['reading_structures'] == []
    o.write(run / 'draft.json', draft or draft_v2(), mutable=True)
    return run


def review(run, failures=None):
    assert main(['organize', 'assess', str(run)]) == 1
    value = load_json(run / 'review-template.json')
    value.update(reviewer='independent synthetic fixture', reviewer_type='synthetic',
                 checked_at='2026-09-28T00:00:00Z', method='Synthetic expected judgments')
    for item in value['judgments']:
        reason = (failures or {}).get(item['target_id'])
        item.update(verdict='fail' if reason else 'pass', reason=reason or 'Synthetic expected faithful case.')
    o.write(run / 'review.json', value)


def test_v2_public_lifecycle_replay_and_export(tmp_path):
    run = run_v2(tmp_path)
    package = load_json(run / 'work-package.json')
    assert package['inputs']['producer'] == o.producer(2)
    assert load_json(run / 'draft.json')['schema_version'] == 'semantic-discourse-draft/v2'
    assert o.file_hash(run / 'MODELING.md') == o.file_hash(o.GUIDE_V2)
    assert main(['organize', 'prepare', str(EXPORT), '--output-dir', str(run), '--format-version', '2']) == 0
    assert main(['organize', 'publish', str(run)]) == 2
    review(run)
    assert main(['organize', 'publish', str(run)]) == 0
    release, = (run / 'releases').iterdir()
    model = load_json(release / 'model.json')
    assert model['schema_version'] == 'diagram-domain-model/v3'
    assert len(model['reading_structures']) == 4
    block = next(s for s in model['reading_structures'] if s['id'] == 'structure:block')
    assert block['reading_order'] == ['element:dry', 'element:water', 'element:unknown-volume']
    assert block['evidence'] and 'refs' not in block
    assert len(model['edges']) == 3  # organization must not manufacture element relations
    before = {str(p.relative_to(release)): p.read_bytes() for p in release.rglob('*') if p.is_file()}
    assert main(['organize', 'replay', str(release / 'manifest.json'), '--output-dir', str(tmp_path / 'clean')]) == 0
    rebuilt, = (tmp_path / 'clean' / 'releases').iterdir()
    assert before == {str(p.relative_to(rebuilt)): p.read_bytes() for p in rebuilt.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation', [
    'duplicate', 'missing', 'overlap', 'wrong-anchor', 'wrong-order', 'short-comparison',
    'short-sequence', 'empty-explanation', 'bad-role', 'stale-ref', 'missing-core-ref', 'scope',
    'collision', 'unknown-pattern', 'duplicate-scope',
])
def test_v2_rejects_structural_negatives(tmp_path, mutation):
    draft = draft_v2()
    chapter, water, limits, block = draft['reading_structures']
    if mutation == 'duplicate': water['core_ids'].append('b-check')
    elif mutation == 'missing': water['independent_ids'].append('b-limits')
    elif mutation == 'overlap': water['independent_ids'].append('b-water')
    elif mutation == 'wrong-anchor': water['supports'][0]['anchor_id'] = 'b-condition'
    elif mutation == 'wrong-order': water['reading_order'].pop()
    elif mutation == 'short-comparison': chapter['core_ids'] = ['t-water']; chapter['independent_ids'] = ['t-limits']
    elif mutation == 'short-sequence': water['core_ids'] = ['b-check']; water['independent_ids'] = ['b-water']
    elif mutation == 'empty-explanation': limits['core_ids'] = []; limits['independent_ids'] = ['b-limits']
    elif mutation == 'bad-role': water['supports'][0]['role'] = 'cause'
    elif mutation == 'stale-ref': chapter['refs'][0]['content_revision'] = '0' * 64
    elif mutation == 'missing-core-ref': chapter['refs'] = [chapter['refs'][0]]
    elif mutation == 'scope': block['scope_id'] = 'absent'
    elif mutation == 'collision': block['id'] = 'b-water'
    elif mutation == 'unknown-pattern': block['pattern'] = 'cause'
    elif mutation == 'duplicate-scope': block['scope_id'] = 't-water'
    run = run_v2(tmp_path, draft)
    assert main(['organize', 'assess', str(run)]) == 2


def test_v2_semantic_negative_is_independently_rejected(tmp_path):
    draft = draft_v2()
    draft['reading_structures'][1]['rationale'] = 'All checks and watering are unconditional every six hours.'
    run = run_v2(tmp_path, draft)
    review(run, {'structure:water': 'Misstates the dry and forecast conditions.'})
    assert main(['organize', 'assess', str(run)]) == 1
    quality = load_json(run / 'quality.json')
    assert quality['program_checks']['source_references'] == 'pass'
    assert any(i['kind'] == 'semantic_review' and i['target_id'] == 'structure:water' for i in quality['issues'])
    assert main(['organize', 'publish', str(run)]) == 2


def test_review_reuse_only_exact_unchanged_units_and_keeps_prior_event(tmp_path):
    prior = tmp_path / 'prior'
    assert main(['organize', 'prepare', str(EXPORT), '--output-dir', str(prior)]) == 0
    o.write(prior / 'draft.json', load_json(FIXTURE / 'garden.draft.json'), mutable=True)
    review(prior)
    run = run_v2(tmp_path)
    assert main(['organize', 'assess', str(run)]) == 1
    assert main(['organize', 'reuse-review', str(run), '--from-review', str(prior / 'review.json')]) == 0
    carry = load_json(run / 'review-reuse-template.json')
    assert all(x['verdict'] == 'pending' for x in carry['judgments'] if x['target_id'].startswith('structure:'))
    assert any(x['verdict'] == 'pass' for x in carry['judgments'] if not x['target_id'].startswith('structure:'))
    assert carry['reviewer'] == 'unknown'
    assert load_json(run / 'reused-review.json') == load_json(prior / 'review.json')
    assert main(['organize', 'publish', str(run)]) == 2
    carry.update(reviewer='new independent synthetic fixture', checked_at='2026-09-28T01:00:00Z')
    for item in carry['judgments']:
        if item['verdict'] == 'pending': item.update(verdict='pass', reason='New synthetic structure judgment.')
    o.write(run / 'review.json', carry)
    assert main(['organize', 'publish', str(run)]) == 0
    release, = (run / 'releases').iterdir()
    assert (release / 'reused-review.json').exists()
    assert main(['organize', 'replay', str(release / 'manifest.json'), '--output-dir', str(tmp_path / 'reuse-clean')]) == 0
