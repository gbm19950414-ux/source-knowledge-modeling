"""Public synthetic safety regressions; never real document review evidence."""
from copy import deepcopy
import pytest
from source_knowledge_modeling import semantic_v3 as v3, semantic_workflow as workflow
from source_knowledge_modeling.core import load_json, KnowledgeFoundationError
from test_semantic import ROOT
from test_semantic_v3 import _workspace, _write
from test_semantic_v3_impact import _chain, _quality, _review_update


def _candidate_workspace(tmp_path):
    run, anchor = _workspace(tmp_path)
    base = load_json(run/'base.json')
    drafts = v3.normalize_drafts(base, load_json(run/'drafts.json'), root=ROOT)
    _write(run/'checked-drafts.json', drafts)
    candidate = v3.candidate_preview(base,drafts,root=ROOT)
    checks = []
    for kind,plural in [('information','information'),('discourse','discourse'),('element','elements'),('relation','relations')]:
        for obj in candidate[plural]:
            row={'target_kind':kind,'target_id':obj['id'],'verdict':obj['status'] if kind in ('information','discourse') and obj['status']!='modeled' else 'pass','reason':'Synthetic fixture verdict'}
            if kind in ('element','relation'):row['content_revision']=obj['content_revision']
            checks.append(row)
    _write(run/'semantic-assessment.json',{'schema_version':'knowledge-semantic-assessment/v1',
        'draft_sha256':v3.digest(drafts),'inventory_sha256':workflow.file_hash(run/'source-information.json'),
        'discourse_sha256':workflow.file_hash(run/'discourse-audit.json'),'reviewer':'synthetic test only',
        'reviewer_type':'AI','model':'unknown','check_method':'source_text','checked_at':'2026-10-05T00:00:00Z','checks':checks})
    return run,base,drafts,candidate


@pytest.mark.parametrize('field',['information','discourse'])
def test_resolved_audit_span_cannot_exceed_frozen_text(tmp_path,field):
    run,base,drafts,candidate=_candidate_workspace(tmp_path)
    candidate[field][0].update(start=999,end=1000)
    with pytest.raises(KnowledgeFoundationError,match='text range'):
        v3.validate_projection(candidate,root=ROOT,resolve=True)


@pytest.mark.parametrize('offsets',[(0,2),(999,1000)])
def test_assessment_rejects_model_audit_evidence_outside_independent_inventory(tmp_path,offsets):
    run,base,drafts,candidate=_candidate_workspace(tmp_path)
    # Parsed drafts have valid object hashes; change only the audit's claimed location.
    drafts['proposals'][0]['upsert_information'][0].update(start=offsets[0],end=offsets[1])
    _write(run/'checked-drafts.json',drafts)
    assessment=load_json(run/'semantic-assessment.json');assessment['draft_sha256']=v3.digest(drafts)
    _write(run/'semantic-assessment.json',assessment)
    if offsets[0]==999:
        with pytest.raises(KnowledgeFoundationError,match='text range'):
            workflow.assess_workspace(run,root=ROOT)
    else:
        report=workflow.assess_workspace(run,root=ROOT)
        assert report['status']=='fail'
        assert any(c['status']=='fail' and c['id']=='information:evidence:meaning-amount' for c in report['checks'])


@pytest.mark.parametrize('field',['information','discourse'])
@pytest.mark.parametrize('mutation',['meaning','binding','add','remove'])
def test_audit_binding_and_coverage_changes_require_scope_review(field,mutation):
    old=_chain();anchor=old['elements'][0]['anchors'][0]
    item={'id':'audit-definition','meaning':'Synthetic definition audit','status':'modeled',
          'locator':anchor['locator'],'start':anchor['start'],'end':anchor['end'],'reason':'Synthetic fixture',
          'bindings':[{'object_id':'definition','content_revision':old['elements'][0]['content_revision'],'field_path':'/text'}]}
    old[field]=[item]
    new=deepcopy(old);new['revision']={'id':'audit-next','parent_id':old['revision']['id'],'note':'Audit mutation'}
    if mutation=='meaning':new[field][0]['meaning']+=' changed'
    elif mutation=='binding':new[field][0]['bindings'][0].update(object_id='bridge',content_revision=next(e['content_revision'] for e in new['elements'] if e['id']=='bridge'))
    elif mutation=='add':new[field].append({**deepcopy(item),'id':'additional-meaning'})
    else:new[field]=[]
    impact=v3.impact(old,new)
    assert impact['changed_element_ids']==impact['changed_relation_ids']==[]
    assert set(impact['recheck_object_ids'])=={e['id'] for e in new['elements']+new['relations']}
    assert impact['rebuild_views'] and impact['rebuild_export']


def test_unknown_semantic_dependencies_default_to_whole_scope():
    old=_chain();new=deepcopy(old);new['revision']={'id':'next','parent_id':old['revision']['id'],'note':'Unknown implicit dependency'}
    # No context/citation edge claims this independent object depends on the definition.
    obj=next(e for e in new['elements'] if e['id']=='definition');obj['text']+=' changed';obj['content_revision']=v3.content_revision(obj,'element')
    impact=v3.impact(old,new)
    assert set(impact['recheck_object_ids'])=={e['id'] for e in new['elements']+new['relations']}
    assert 'semantic_dependency_completeness_unknown' in impact['conservative_recheck_reasons']


def test_publication_cannot_reuse_unproven_independent_object_reviews():
    old=_chain();changed=deepcopy(old['elements']);changed[0]['text']+=' changed'
    changed=[e for e in changed if e['id']!='independent']
    proposal={'id':'all-declared-only','reason':'Synthetic all declared dependencies but no completeness proof',
        'rule_ids':['IS3-03'],'upsert_elements':changed,'upsert_relations':deepcopy(old['relations']),
        'lineage':[{'old_kind':'element','old_id':'definition','new_kind':'element','new_ids':['definition'],'change':'replaced','reason':'Synthetic'}]}
    with pytest.raises(KnowledgeFoundationError,match='fresh review.*independent'):
        _review_update(old,proposal,'unknown-incomplete')


@pytest.mark.parametrize('field',['information','discourse'])
def test_publication_audit_only_change_cannot_reuse_other_object_reviews(field):
    old=_chain();anchor=old['elements'][0]['anchors'][0]
    audit={'id':'audit-definition','meaning':'Synthetic definition audit','status':'modeled',
           'locator':anchor['locator'],'start':anchor['start'],'end':anchor['end'],'reason':'Synthetic fixture',
           'bindings':[{'object_id':'definition','content_revision':old['elements'][0]['content_revision'],'field_path':'/text'}]}
    old[field]=[audit]
    audit=deepcopy(audit);audit['meaning']+=' changed'
    proposal={'id':'audit-only','reason':'Synthetic audit-only change','rule_ids':['IS3-03'],
              'upsert_elements':[deepcopy(old['elements'][0])],'upsert_'+field:[audit]}
    with pytest.raises(KnowledgeFoundationError,match='fresh review'):
        _review_update(old,proposal,'audit-incomplete')
    proposal['upsert_elements']=deepcopy(old['elements'])
    proposal['upsert_relations']=deepcopy(old['relations'])
    new=_review_update(old,proposal,'audit-complete')
    assert new[field][0]['meaning']==audit['meaning']
    assert all(obj['review_id']!=old_obj['review_id'] for obj,old_obj in zip(new['elements'],old['elements']))


def test_presentation_change_keeps_projection_and_review_events_byte_identical():
    from source_knowledge_modeling.reading_view_v3 import default_spec, render_reading_view_v3
    projection=_chain();before=v3.canonical_bytes(projection)
    events=v3.canonical_bytes({'reviews':projection['reviews'],'checks':projection['source_checks']})
    first=render_reading_view_v3(projection,before)
    spec=default_spec(projection,before);spec['title']='Synthetic presentation change'
    second=render_reading_view_v3(projection,before,spec)
    assert first!=second
    assert before==v3.canonical_bytes(projection)
    assert events==v3.canonical_bytes({'reviews':projection['reviews'],'checks':projection['source_checks']})


def test_stale_guide_cannot_reuse_a_passing_quality_report(monkeypatch):
    base=_chain();draft={'fixture':'not executed'};quality=_quality(base,draft)
    monkeypatch.setattr(workflow,'guide_hash',lambda:'f'*64)
    with pytest.raises(KnowledgeFoundationError,match='guide'):
        v3._check_quality(base,draft,quality)


@pytest.mark.parametrize('checked_scope',[['line:5'],['line:2']])
def test_source_silence_requires_same_independently_checked_scope(tmp_path,checked_scope):
    from test_semantic import GARDEN
    from source_knowledge_modeling.cli import main
    seed,_,_,_=_candidate_workspace(tmp_path/'seed')
    scope=tmp_path/'scope.json';run=tmp_path/'run'
    _write(scope,{'included_locators':['line:5'],'context_locators':['line:2'],'description':'Synthetic silence scope'})
    assert main(['prepare',str(GARDEN),'--root',str(ROOT),'--profile','instructional-source@3.0.0',
                 '--scope',str(scope),'--output-dir',str(run)])==0
    base=load_json(run/'base.json')
    for name in ('source-information.json','discourse-audit.json','drafts.json','semantic-assessment.json'):
        _write(run/name,load_json(seed/name))
    inventory=load_json(run/'discourse-audit.json')
    inventory['items'][0].update(status='not_stated',critical=True,checked_scope=['line:5'])
    _write(run/'discourse-audit.json',inventory)
    authored=load_json(run/'drafts.json')
    authored['proposals'][0]['upsert_discourse'][0].update(status='not_stated',checked_scope=checked_scope)
    drafts=v3.normalize_drafts(base,authored,root=ROOT)
    _write(run/'checked-drafts.json',drafts)
    assessment=load_json(run/'semantic-assessment.json')
    assessment.update(draft_sha256=v3.digest(drafts),discourse_sha256=workflow.file_hash(run/'discourse-audit.json'))
    next(c for c in assessment['checks'] if c['target_kind']=='discourse')['verdict']='not_stated'
    _write(run/'semantic-assessment.json',assessment)
    report=workflow.assess_workspace(run,root=ROOT)
    assert report['status']==('pass' if checked_scope==['line:5'] else 'fail')
    if checked_scope==['line:2']:
        assert any(c['id']=='discourse:checked-scope:discourse-none' and c['status']=='fail' for c in report['checks'])


def test_changed_installed_profile_refuses_prior_acceptance(monkeypatch):
    old=_chain();new=deepcopy(old);new['revision']={'id':'next','parent_id':old['revision']['id'],'note':'New installed rule identity'}
    profile=deepcopy(old['profile']);profile['definition_sha256']='1'*64
    monkeypatch.setattr(v3,'profile_manifest',lambda *_:profile)
    with pytest.raises(KnowledgeFoundationError,match='profile definition hash mismatch'):
        v3.impact(old,new)


def test_ambiguous_quote_is_not_resumed_with_an_old_location(tmp_path):
    run,anchor=_workspace(tmp_path);base=load_json(run/'base.json')
    with pytest.raises(KnowledgeFoundationError,match='ambiguous'):
        v3.resolve_anchor(base,{'locator':'line:5','quote':'没有'},root=ROOT)
    resolved=v3.resolve_anchor(base,{'locator':'line:5','quote':'没有','start':4,'end':6},root=ROOT)
    assert resolved['start']==4 and resolved['end']==6


def test_identical_source_inspection_does_not_append_a_new_event():
    base=_chain();anchor=base['elements'][0]['anchors'][0]
    args=dict(check_id='exact-reuse',anchors=[anchor],reviewer='synthetic fixture only',method='source_text',note='Same fixed inspection',root=ROOT)
    once=v3.record_source_check(base,v3.new_source_check_ledger(base),**args)
    twice=v3.record_source_check(base,once,**args)
    assert v3.canonical_bytes(once)==v3.canonical_bytes(twice)
    assert len(twice['checks'])==len(once['checks'])==1
