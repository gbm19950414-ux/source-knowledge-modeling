"""Public organization lifecycle; synthetic review records are explicitly fixtures."""
from pathlib import Path
import pytest
from source_knowledge_modeling import organize as o
from source_knowledge_modeling.cli import main
from source_knowledge_modeling.core import load_json

FIXTURE = Path(__file__).resolve().parents[1] / 'contracts/semantic-discourse/v1/examples'


def workspace(tmp_path, requirements=False):
    run = tmp_path / 'run'
    args = ['organize', 'prepare', str(FIXTURE / 'garden.export.json'), '--output-dir', str(run)]
    if requirements:
        o.write(tmp_path / 'requirements.json', {'must_preserve': ['dry AND no rain in six hours', 'no volume specified']})
        args += ['--requirements', str(tmp_path / 'requirements.json')]
    assert main(args) == 0
    o.write(run / 'draft.json', load_json(FIXTURE / 'garden.draft.json'), mutable=True)
    assert main(args) == 0  # resume preserves the completed draft
    assert load_json(run / 'draft.json')['blocks']
    return run


def synthetic_review(run, failures=None):
    assert main(['organize', 'assess', str(run)]) == 1
    review = load_json(run / 'review-template.json')
    review.update(reviewer='synthetic independent fixture reviewer', reviewer_type='synthetic',
                  checked_at='2026-09-27T00:01:00Z', method='Synthetic expected judgments, not real review events')
    for judgment in review['judgments']:
        reason = (failures or {}).get(judgment['target_id'])
        judgment.update(verdict='fail' if reason else 'pass', reason=reason or 'Synthetic expected faithful case.')
    o.write(run / 'review.json', review, mutable=True)


def test_public_organization_publish_replay_and_frozen_requirements(tmp_path):
    run = workspace(tmp_path, requirements=True)
    assert main(['organize', 'publish', str(run)]) == 2
    synthetic_review(run)
    assert main(['organize', 'assess', str(run)]) == 0
    assert main(['organize', 'publish', str(run)]) == 0
    release, = (run / 'releases').iterdir()
    before = {p.relative_to(release): p.read_bytes() for p in release.rglob('*') if p.is_file()}
    assert main(['organize', 'publish', str(run)]) == 0
    assert before == {p.relative_to(release): p.read_bytes() for p in release.rglob('*') if p.is_file()}
    clean = tmp_path / 'clean'
    replay_args = ['organize', 'replay', str(release / 'manifest.json'), '--output-dir', str(clean)]
    assert main(replay_args) == main(replay_args) == 0
    replayed, = (clean / 'releases').iterdir()
    assert before == {p.relative_to(replayed): p.read_bytes() for p in replayed.rglob('*') if p.is_file()}
    model = load_json(release / 'model.json')
    assert model['reading_order'] == ['element:check', 'element:dry', 'element:water', 'element:unknown-volume']
    assert len(model['edges']) == 3  # original source edge plus two reviewed high-level links
    block = next(n for n in model['nodes'] if n['id'] == 'b-water')
    assert block['children'] == ['element:water'] and block['related_element_ids'] == ['element:dry', 'element:unknown-volume']
    assert all('excerpt' in e and 'location' in e for e in load_json(release / 'evidence.json')['sources'].values())
    assert 'actual-reviewer' not in (run / 'RUNBOOK.md').read_text()
    o.write(run / 'requirements.json', {'weakened': True}, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 2


@pytest.mark.parametrize('mutation', ['missing-member', 'duplicate-member', 'absent-related', 'bad-pointer',
                                     'wrong-rollup', 'wrong-direction', 'missing-premises', 'missing-uncertainty', 'unreviewed-source'])
def test_public_assess_rejects_broken_contract(tmp_path, mutation):
    run = workspace(tmp_path)
    draft = load_json(run / 'draft.json')
    if mutation == 'missing-member': draft['blocks'].pop()
    elif mutation == 'duplicate-member': draft['blocks'][1]['element_ids'].append('check')
    elif mutation == 'absent-related': draft['blocks'][0]['related_element_ids'] = ['absent']
    elif mutation == 'bad-pointer': draft['chapter']['summary'][0]['refs'][0]['field_path'] = 'text'
    elif mutation == 'wrong-rollup': draft['edges'][0]['from_id'] = 'b-check'
    elif mutation == 'wrong-direction': draft['edges'][0]['direction'] = 'symmetric'
    elif mutation == 'missing-premises': draft['edges'][1]['premises'] = []
    elif mutation == 'missing-uncertainty': draft['edges'][1]['uncertainty'] = ''
    elif mutation == 'unreviewed-source':
        source = load_json(run / 'source.json'); source['elements'][0]['review_id'] = 'absent'
        o.write(tmp_path / 'unreviewed.json', source)
        assert main(['organize', 'prepare', str(tmp_path / 'unreviewed.json'), '--output-dir', str(tmp_path / 'bad')]) == 2
        return
    o.write(run / 'draft.json', draft, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 2


@pytest.mark.parametrize(('target', 'text', 'reason'), [
    ('s-water', '志愿者每六小时浇水。', '把未来六小时无雨的预报窗口错置为操作频率，并遗漏干燥条件。'),
    ('s-limits', '说明给出了单次浇水量。', '遗漏否定，反转资料边界。'),
    ('s-water', '所有操作都必须无雨才能执行。', '把一条局部条件泛化到检查等全部操作。'),
])
def test_legal_semantic_negatives_need_actual_judgment_not_hash_failure(tmp_path, target, text, reason):
    run = workspace(tmp_path)
    draft = load_json(run / 'draft.json')
    for block in draft['blocks']:
        for summary in block['summary']:
            if summary['id'] == target: summary['text'] = text
    o.write(run / 'draft.json', draft, mutable=True)
    # Normalization succeeds and the independent fixture judges this exact new revision.
    synthetic_review(run, {target: reason})
    assert main(['organize', 'assess', str(run)]) == 1
    quality = load_json(run / 'quality.json')
    assert quality['program_checks']['source_references'] == 'pass'
    assert quality['issues'] == [{'kind': 'semantic_review', 'target_id': target, 'reason': reason}]
    assert main(['organize', 'publish', str(run)]) == 2


def test_inference_cannot_be_relabelled_source_and_review_is_invalidated(tmp_path):
    run = workspace(tmp_path)
    synthetic_review(run)
    assert main(['organize', 'assess', str(run)]) == 0
    draft = load_json(run / 'draft.json')
    draft['edges'][1]['origin'] = 'source'
    o.write(run / 'draft.json', draft, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 2
    draft['edges'][1]['origin'] = 'interpretation'
    draft['edges'][1]['uncertainty'] = '新的解释边界，必须重新审阅。'
    o.write(run / 'draft.json', draft, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 1
    assert any(i['kind'] == 'dependency' for i in load_json(run / 'quality.json')['issues'])


def test_source_evidence_and_leaf_namespaces_cannot_be_overwritten(tmp_path):
    run = workspace(tmp_path)
    draft = load_json(run / 'draft.json')
    draft['chapter']['summary'][0]['id'] = 'source:check'
    o.write(run / 'draft.json', draft, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 2
    draft['chapter']['summary'][0]['id'] = 'unique-summary'
    draft['chapter']['id'] = 'element:check'
    o.write(run / 'draft.json', draft, mutable=True)
    assert main(['organize', 'assess', str(run)]) == 2
