"""Synthetic dependency fixtures; these tests do not record real source judgments."""
from copy import deepcopy
import pytest
from source_knowledge_modeling import semantic_v3 as v3
from source_knowledge_modeling.core import load_json, KnowledgeFoundationError
from source_knowledge_modeling.semantic import profile_manifest
from test_semantic import ROOT, GARDEN


def _quality(base, drafts):
    from source_knowledge_modeling.semantic_workflow import guide_hash
    report = {'schema_version': 'knowledge-semantic-quality/v1', 'artifact_id': base['artifact_id'],
              'base_sha256': v3.digest(base), 'draft_sha256': v3.digest(drafts),
              'profile_sha256': base['profile']['definition_sha256'], 'status': 'pass',
              'input_hashes': {**{key:'0'*64 for key in ('inventory','discourse','semantic_assessment','work_package')}, 'guide':guide_hash()},
              'checks': [{'id':'synthetic','category':'fixture','status':'pass','detail':'Synthetic lifecycle only'}], 'gaps':[],
              'reviewer': {'reviewer':'synthetic fixture only','reviewer_type':'AI','model':'unknown','check_method':'source_text','checked_at':'2026-10-05T00:00:00Z'},
              'note': 'Synthetic lifecycle fixture; not an independent judgment of real material.'}
    report['report_sha256'] = v3.digest(report)
    return report


def _review_update(base, proposal, revision):
    proposal = deepcopy(proposal)
    for plural in ('upsert_elements', 'upsert_relations'):
        for item in proposal.get(plural, []):
            for key in ('source_check_ids', 'review_state', 'review_id', 'legacy_review_package_id'):
                item.pop(key, None)
    authored = {'schema_version': v3.DRAFT_VERSION, 'artifact_id': base['artifact_id'],
                'base_revision_id': base['revision']['id'], 'proposals': [proposal]}
    drafts = v3.normalize_drafts(base, authored, root=ROOT)
    quality = _quality(base, drafts)
    checks = v3.record_source_check(base, v3.new_source_check_ledger(base), check_id='fixture-check-'+revision,
                anchors=[drafts['proposals'][0]['upsert_elements'][0]['anchors'][0]],
                reviewer='synthetic fixture only', method='source_text', note='Synthetic test source', root=ROOT)
    ledger = v3.record_decision(base, drafts, v3.new_review_ledger(base, drafts, quality), checks, quality,
                proposal_id=proposal['id'], decision='accept', reviewer='synthetic fixture only',
                checked_anchors=[v3.anchor_key(drafts['proposals'][0]['upsert_elements'][0]['anchors'][0])],
                check_method='source_text', note='Synthetic lifecycle input, not real independent review', root=ROOT)
    return v3.publish(base, drafts, ledger, checks, quality, revision_id=revision, root=ROOT)


def _chain():
    foundation = load_json(GARDEN)
    base = v3.new_projection(foundation, GARDEN.read_bytes(),
                {'included_locators': ['line:5'], 'description': 'Synthetic dependency test'},
                profile_manifest('instructional-source', '3.0.0'), root=ROOT)
    anchor = v3.resolve_anchor(base, {'locator': 'line:5', 'quote': '没有给出单次浇水量'}, root=ROOT)
    dependencies = {'definition': [], 'qualification': ['definition'], 'downstream': ['qualification'],
                    'bridge': [], 'observer': ['premise-relation'], 'independent': []}
    elements = [{'id': ident, 'kind': 'assertion', 'text': 'Synthetic fixture '+ident,
                 'origin_type': 'source_claim', 'modality': 'asserted', 'negated': False,
                 'anchors': [anchor], 'context_ids': deps} for ident, deps in dependencies.items()]
    relation = {'id': 'premise-relation', 'type': 'supports', 'from_id': 'bridge', 'to_id': 'qualification',
                'origin_type': 'source_claim', 'basis': 'explicit', 'premises': ['qualification'],
                'context_ids': ['qualification'], 'note': 'Synthetic premise relationship', 'anchors': [anchor]}
    proposal = {'id': 'fixture-initial', 'reason': 'Synthetic dependency regression fixture', 'rule_ids': ['IS3-03'],
                'upsert_elements': elements, 'upsert_relations': [relation],
                'upsert_packages': [{'id': 'fixture-package', 'summary': 'Synthetic fixture',
                    'origin_type': 'source_claim', 'modality': 'asserted',
                    'element_ids': list(dependencies), 'relation_ids': ['premise-relation'],
                    'context_ids': [], 'legacy_review': None}]}
    return _review_update(base, proposal, 'fixture-base')


def _changed(old):
    later = deepcopy(old)
    later['revision'] = {'id': 'fixture-next', 'parent_id': old['revision']['id'], 'note': 'Synthetic update'}
    item = next(x for x in later['elements'] if x['id'] == 'definition')
    item['text'] += ' changed'
    item.update(source_check_ids=[], review_state='pending_semantic', review_id=None)
    item['content_revision'] = v3.content_revision(item, 'element')
    return later


def test_impact_unchanged_reviewed_chain_reuses_all_objects():
    old = _chain(); new = deepcopy(old)
    new['revision'] = {'id': 'next', 'parent_id': old['revision']['id'], 'note': 'No semantic change'}
    result = v3.impact(old, new)
    assert result['recheck_object_ids'] == []
    assert not result['rebuild_views'] and not result['rebuild_export']


def test_impact_follows_transitive_context_premise_and_relation_references():
    old = _chain(); new = _changed(old)
    result = v3.impact(old, new)
    assert result['dependent_object_ids'] == ['downstream', 'observer', 'premise-relation', 'qualification']
    assert set(result['recheck_object_ids']) == {x['id'] for x in new['elements']+new['relations']}
    assert new['elements'][2]['text'] == old['elements'][2]['text']
    assert 'independent' in result['recheck_object_ids']


def test_impact_dependency_cycle_terminates_and_keeps_independent_objects():
    old = _chain()
    item = next(x for x in old['elements'] if x['id'] == 'qualification')
    item['context_ids'].append('downstream')
    item['content_revision'] = v3.content_revision(item, 'element')
    # Synchronize relation endpoint hash after changing the synthetic graph.
    r = old['relations'][0]; r['to_revision'] = item['content_revision'];r['content_revision'] = v3.content_revision(r, 'relation')
    result = v3.impact(old, _changed(old))
    assert set(result['dependent_object_ids']) == {'qualification','downstream','premise-relation','observer'}
    assert set(result['recheck_object_ids']) == {x['id'] for x in old['elements']+old['relations']}


@pytest.mark.parametrize('change', ['source', 'profile', 'scope'])
def test_impact_unknown_input_identity_or_scope_expands_conservatively(change):
    old = _chain(); new = deepcopy(old)
    new['revision'] = {'id': 'next', 'parent_id': old['revision']['id'], 'note': 'Changed input contract'}
    if change == 'source':
        new['source']['snapshot_path'] = 'other-frozen-snapshot.json'
    elif change == 'profile':
        new['profile']['definition_sha256'] = '0' * 64
        with pytest.raises(KnowledgeFoundationError, match='profile definition hash mismatch'):
            v3.impact(old, new)
        return
    else:
        new['scope']['description'] += ' changed contract requiring fresh coverage judgment'
    assert set(v3.impact(old, new)['recheck_object_ids']) == {x['id'] for x in new['elements']+new['relations']}


def test_public_publish_requires_fresh_transitive_review_even_if_middle_text_unchanged():
    old = _chain()
    a = deepcopy(next(x for x in old['elements'] if x['id'] == 'definition'));a['text'] += ' changed'
    b = deepcopy(next(x for x in old['elements'] if x['id'] == 'qualification'))
    r = deepcopy(old['relations'][0])
    proposal = {'id':'fixture-partial-update','reason':'Synthetic root change and unchanged direct dependencies',
                'rule_ids':['IS3-03'],'upsert_elements':[a,b],'upsert_relations':[r],
                'lineage':[{'old_kind':'element','old_id':'definition','reason':'Synthetic changed definition',
                            'new_ids':['definition'],'new_kind':'element','change':'replaced'}]}
    # The intermediate qualification and relation get new reviews, but downstream/observer do not.
    with pytest.raises(KnowledgeFoundationError, match='dependency changed without fresh review'):
        _review_update(old, proposal, 'fixture-incomplete')


def test_public_publish_accepts_fresh_review_of_whole_scope_with_unknown_dependencies():
    old = _chain()
    touched = {x['id'] for x in old['elements']}
    elements = deepcopy([x for x in old['elements'] if x['id'] in touched])
    next(x for x in elements if x['id'] == 'definition')['text'] += ' changed'
    proposal = {'id':'fixture-complete-update','reason':'Synthetic complete dependency review',
                'rule_ids':['IS3-03'],'upsert_elements':elements,'upsert_relations':deepcopy(old['relations']),
                'lineage':[{'old_kind':'element','old_id':'definition','new_kind':'element',
                            'new_ids':['definition'],'change':'replaced','reason':'Synthetic changed definition'}]}
    new = _review_update(old, proposal, 'fixture-complete')
    before = {x['id']:x for x in old['elements']+old['relations']}
    after = {x['id']:x for x in new['elements']+new['relations']}
    assert before['qualification']['text'] == after['qualification']['text']
    assert before['qualification']['content_revision'] == after['qualification']['content_revision']
    for ident in touched | {'premise-relation'}:
        assert before[ident]['review_id'] != after[ident]['review_id']


def test_scope_extension_invalidates_prior_coverage_even_without_object_changes():
    old = _chain()
    foundation = load_json(GARDEN)
    new = v3.new_projection(foundation, GARDEN.read_bytes(),
                {'included_locators':['line:2','line:5'],'description':'Extended synthetic primary coverage'},
                profile_manifest('instructional-source','3.0.0'),root=ROOT)
    for key in ('elements','relations','packages','occurrences','reviews','source_checks'):
        new[key] = deepcopy(old[key])
    new['revision'] = {'id':'extended','parent_id':old['revision']['id'],'note':'New primary source unit'}
    result = v3.impact(old,new)
    assert result['changed_element_ids'] == result['changed_relation_ids'] == []
    assert set(result['recheck_object_ids']) == {x['id'] for x in old['elements']+old['relations']}
    assert result['rebuild_views'] and result['rebuild_export']
