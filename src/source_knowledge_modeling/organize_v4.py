"""Reviewed typed blocks and precise higher relations over source export v4."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shlex
from typing import Any

from .core import load_json
from .semantic_v3 import digest
from . import organize as o
from . import organize_v3 as v3

VERSION = 4
GUIDE = o.RESOURCES / 'templates' / 'discourse-modeling-v4.md'
HUMANIZER = o.RESOURCES / 'resources' / 'humanizer'
TAXONOMY = o.RESOURCES / 'resources' / 'content-types-v1.json'


def resource_hashes() -> dict[str, str]:
    return {str(p.relative_to(o.RESOURCES)): o.file_hash(p) for p in sorted(HUMANIZER.rglob('*')) if p.is_file()}


def guide_hash() -> str:
    return digest({'guide_file_sha256':o.file_hash(GUIDE),'humanizer_sha256':digest(resource_hashes()),
                   'taxonomy_sha256':o.file_hash(TAXONOMY)})


def producer() -> dict:
    names = [o.RESOURCES / 'organize_v4.py', o.RESOURCES / 'organize_v3.py', o.RESOURCES / 'organize.py',
             o.RESOURCES / 'cli.py', GUIDE, TAXONOMY,
             o.RESOURCES / 'schemas' / 'semantic-discourse-draft-v4.schema.json',
             o.RESOURCES / 'schemas' / 'semantic-discourse-model-v4.schema.json']
    return {'name': 'source-knowledge-modeling', 'component': 'organize/v4',
            'implementation_sha256': digest({str(p.relative_to(o.RESOURCES)): o.file_hash(p) for p in names}),
            'guide_sha256': guide_hash(), 'humanizer_sha256': digest(resource_hashes())}


# Preserve v3's source snapshot and document-directory semantics exactly.
verified_units = v3.verified_units
directory_template = v3.directory_template
extract_directory = v3.extract_directory
verify_directory = v3.verify_directory


def prepare(export_path: Path, output_dir: Path, requirements_path: Path | None, root: Path, document_map_path: Path | None) -> dict:
    source = load_json(export_path)
    o.source_index(source)
    units = verified_units(source, root)
    directory = extract_directory(load_json(document_map_path) if document_map_path else directory_template(source, units), source, units, supplied=bool(document_map_path))
    requirements = load_json(requirements_path) if requirements_path else None
    scope_members,scope_occurrences,_ = _members({'containers':directory['containers'],'blocks':[],
        'paragraphs':directory['paragraphs'],'root_id':directory['root_id']},o.source_index(source)[0],source)
    scope_report={'export_elements':len(source['elements']),'export_occurrences':len(source['occurrences']),
                  'selected_elements':len(scope_members[directory['root_id']]),
                  'selected_occurrences':len(scope_occurrences),
                  'prior_map_occurrences':len(load_json(document_map_path).get('occurrences',[])) if document_map_path else None}
    identity = {'source_sha256': digest(source), 'source_file_sha256': o.file_hash(export_path),
                'root': str(root.resolve()), 'snapshot_sha256': source['source']['snapshot_sha256'],
                'document_map_sha256': digest(directory), 'requirements_sha256': digest(requirements) if requirements is not None else None,
                'content_types_sha256': o.file_hash(TAXONOMY),
                'producer': producer()}
    run = output_dir.resolve()
    if (run / 'work-package.json').exists():
        package = load_json(run / 'work-package.json')
        if package['inputs'] != identity or digest(load_json(run / 'source.json')) != identity['source_sha256'] or o.file_hash(run / 'MODELING.md') != o.file_hash(GUIDE) or o.file_hash(run / 'content-types.json') != o.file_hash(TAXONOMY):
            o.fail('Workspace inputs/guide changed; prepare a new directory')
        return {'run_dir': str(run), 'reused': True, 'input_fingerprint': digest(identity)}
    if run.exists() and any(run.iterdir()):
        o.fail('New workspace must be empty')
    draft = {'schema_version': 'semantic-discourse-draft/v4', 'source_sha256': digest(source), 'author': 'unknown',
             'directory_status': directory['verification'], 'root_id': directory['root_id'], 'containers': directory['containers'],
             'blocks': [], 'edges': [], 'relation_bindings': [], 'reading_structures': []}
    qrun = shlex.quote(str(run))
    prepare_command=(f'skm organize prepare {shlex.quote(str(export_path.resolve()))} --root {shlex.quote(str(root.resolve()))} '
                     f'--output-dir {qrun} --format-version 4'
                     +(f' --document-map {shlex.quote(str(document_map_path.resolve()))}' if document_map_path else '')
                     +(f' --requirements {shlex.quote(str(requirements_path.resolve()))}' if requirements_path else ''))
    for name, value in [('source.json', source), ('document-map.json', directory), ('content-types.json',TAXONOMY.read_text()), ('draft.json', draft),
                        ('MODELING.md', GUIDE.read_text()), ('work-package.json', {
                            'schema_version': 'semantic-discourse-work-package/v1', 'inputs': identity,
                            'prepare_command':prepare_command,
                            'input_fingerprint': digest(identity), 'scope': scope_report,
                            'requirements': 'Review every chapter, section, relation and frozen requirement independently.'})]:
        o.write(run / name, value)
    if requirements is not None:
        o.write(run / 'requirements.json', requirements)
    o.write(run / 'RUNBOOK.md', '# 篇章组织 v4\n\n核对 document-map.json，编辑 draft.json；assess 产生待审模板，不产生批准。\n\n```sh\n'
            + prepare_command
            + f'\nskm organize assess {qrun}\nskm organize publish {qrun}\n```\n')
    return {'run_dir': str(run), 'reused': False, 'input_fingerprint': digest(identity),
            'directory_status': draft['directory_status'], 'scope': scope_report}

LABELS = v3.LABELS
_refs = v3._refs
_members = v3._members


def normalize(source:dict,draft:dict,directory:dict,units:list[dict])->dict:
    o.schema(draft,'semantic-discourse-draft-v4.schema.json')
    elements,relations,_=o.source_index(source)
    objects={**elements,**relations}
    if draft['source_sha256']!=digest(source) or draft['author'].strip()=='unknown':
        o.fail('Draft source or actual author identity missing')
    # Reuse v3's document and reading-structure validation without sending v4
    # semantics through the old high-edge rules.
    legacy=deepcopy(draft)
    legacy['schema_version']='semantic-discourse-draft/v3'
    legacy.pop('relation_bindings')
    legacy['blocks']=[{k:v for k,v in b.items() if k in {'id','parent_id','title','order','element_ids','related_element_ids'}} for b in draft['blocks']]
    legacy['edges']=[]
    base=v3.normalize(source,legacy,directory,units)
    result=deepcopy(draft)
    result['containers']=base['containers'];result['paragraphs']=base['paragraphs']
    result['reading_structures']=base['reading_structures']
    members,_,_=v3._members(result,elements,source)
    registered={x['id'] for x in load_json(TAXONOMY)['entries']}
    for block in result['blocks']:
        if not set(block['content_types'])<=registered:
            o.fail('Unknown content type')
        block['focus_refs']=_refs(block['focus_refs'],objects)
        if not any(r['object_id'] in block['element_ids'] for r in block['focus_refs']):
            o.fail('Block focus must reference a member')
    bindings={}
    for item in result['relation_bindings']:
        rid=item['source_relation_id']
        if rid not in relations or rid in bindings:
            o.fail('Binding needs one existing projected source relation')
        item['qualifiers']=_qualifiers(item['qualifiers'],objects)
        bindings[rid]=item['qualifiers']
    nodes=o.index(result['containers']+result['blocks'],'organization node')
    reserved=set(objects)|{'element:'+x for x in elements}|{'relation:'+x for x in relations}
    edges=o.index(result['edges'],'organization edge')
    if set(edges)&(set(nodes)|reserved):o.fail('Higher edge identity collision')
    for edge in result['edges']:
        if edge['type'] not in LABELS or edge['from_id'] not in members or edge['to_id'] not in members or edge['from_id']==edge['to_id']:
            o.fail('Higher edge needs typed distinct scoped endpoints')
        if edge['direction']!=('symmetric' if edge['type']=='contrasts_with' else 'directed'):
            o.fail('Higher edge changes type direction')
        for key,endpoint in (('from_element_ids','from_id'),('to_element_ids','to_id')):
            ids=edge[key]
            if not ids or len(ids)!=len(set(ids)) or not set(ids)<=members[edge[endpoint]]:
                o.fail('Higher edge needs exact scoped endpoint members')
        edge['premises']=_refs(edge['premises'],objects)
        edge['qualifiers']=_qualifiers(edge.get('qualifiers',[]),objects)
        support=edge.get('source_relation_ids',[])
        if len(support)!=len(set(support)) or not set(support)<=set(relations):o.fail('Invalid supporting relation')
        form=edge['relation_form']
        if form=='source_native':
            if edge['origin']!='source' or edge['basis'] not in {'explicit','inferred_from_source'} or support or not edge['premises'] or not edge.get('scope_note','').strip():
                o.fail('Invalid source_native identity, basis, premises or scope')
            if any(objects[p['object_id']]['origin_type'] not in {'source_claim','source_example'} for p in edge['premises']):
                o.fail('Native relation premises must be source claims or examples')
            if any(r['type']==edge['type'] and r['origin_type'] in {'source_claim','source_example'} and (
                {r['from_id']}==set(edge['from_element_ids']) and {r['to_id']}==set(edge['to_element_ids']) or
                edge['direction']=='symmetric' and {r['to_id']}==set(edge['from_element_ids']) and {r['from_id']}==set(edge['to_element_ids']))
                for r in relations.values()):
                o.fail('Native edge duplicates a source relation; reference it instead')
        elif form=='project_inference':
            if edge['origin']!='interpretation' or edge['basis']!='project_interpretation' or support or not edge['premises'] or any(not edge.get(k,'').strip() for k in ('overall_effect','rationale','scope_note')):
                o.fail('Invalid project inference identity, premises or reasoning')
            if any(objects[p['object_id']]['origin_type'] not in {'source_claim','source_example'} for p in edge['premises']):
                o.fail('Project inference needs source premises')
        elif form=='source_rollup':
            if edge['origin']!='source' or edge['basis']!='source_rollup' or not support or not edge.get('scope_note','').strip():
                o.fail('Invalid source rollup identity, support or scope')
            pairs=[]
            for rid in support:
                r=relations[rid]
                if r['type']!=edge['type'] or r['origin_type'] not in {'source_claim','source_example'}:
                    o.fail('Rollup changes source type or identity')
                a,b=r['from_id'],r['to_id']
                if edge['direction']=='symmetric' and a not in edge['from_element_ids'] and b in edge['from_element_ids']:
                    a,b=b,a
                pairs.append((rid,a,b))
            if {a for _,a,_ in pairs}!=set(edge['from_element_ids']) or {b for _,_,b in pairs}!=set(edge['to_element_ids']):
                o.fail('Rollup members must exactly equal source endpoint unions')
            if edge['qualifiers'] and any(edge['qualifiers']!=bindings.get(rid,[]) for rid in support):
                o.fail('Rollup qualifier must hold for every source relation')
        else:o.fail('Unknown relation form')
    result['schema_version']='semantic-discourse-model/v4'
    result['source_revision']=source['artifact_revision_id']
    result['guide_sha256']=guide_hash()
    result['document_sha256']=digest(directory)
    result['revision']=digest({**result,'content_types_sha256':o.file_hash(TAXONOMY)})
    o.schema(result,'semantic-discourse-model-v4.schema.json')
    return result


def _qualifiers(values:list[dict],objects:dict)->list[dict]:
    result=[]
    for binding in values:
        ref=_refs([binding['source_ref']],objects)[0]
        result.append({'applies_to':binding['applies_to'],'dimension':binding['dimension'],'source_ref':ref})
    if len({digest(x) for x in result})!=len(result):o.fail('Repeated qualifier binding')
    return result


def requirement_units(requirements:Any)->list[dict]:
    if requirements is None:return []
    if isinstance(requirements,dict):
        groups=[(key,value) for key,value in sorted(requirements.items())]
    else:groups=[('requirements',requirements)]
    result=[]
    for key,value in groups:
        entries=value if isinstance(value,list) else [value]
        for i,item in enumerate(entries):
            raw=item.get('id') if isinstance(item,dict) else None
            identifier=raw if isinstance(raw,str) and raw and all(c.isalnum() or c in '_.:-' for c in raw) else f'{key}:{i}'
            result.append({'target_id':('requirement:' if key=='requirements' else 'requirement:'+key+':')+identifier,
                           'content_sha256':digest({'group':key,'item':item})})
    if len({x['target_id'] for x in result})!=len(result):o.fail('Duplicate frozen requirement ID')
    return result


def review_units(model:dict,requirements:Any)->list[dict]:
    endpoints={x['id']:x for x in [*model['containers'],*model['blocks']]}
    bindings={x['source_relation_id']:x for x in model['relation_bindings']}
    units=[]
    for x in [*model['containers'],*model['blocks'],*model['edges'],*model['reading_structures']]:
        dependency={'item':x,'content_types_sha256':o.file_hash(TAXONOMY)}
        if 'relation_form' in x:
            dependency['endpoint_scopes']=[endpoints[x['from_id']],endpoints[x['to_id']]]
            dependency['relation_bindings']=[bindings[r] for r in x.get('source_relation_ids',[]) if r in bindings]
        units.append({'target_id':x['id'],'content_sha256':digest(dependency)})
    units.extend({'target_id':'binding:'+x['source_relation_id'],'content_sha256':digest(x)} for x in model['relation_bindings'])
    units.append({'target_id':'directory:verification','content_sha256':digest({'directory_status':model['directory_status'],'document_sha256':model['document_sha256'],'containers':model['containers'],'paragraphs':model['paragraphs']})})
    units.extend(requirement_units(requirements))
    if len({u['target_id'] for u in units})!=len(units):o.fail('Review target identity collision')
    return units


def assess(run:Path)->dict:
    run=run.resolve()
    source=load_json(run/'source.json');package=load_json(run/'work-package.json')
    identity=package['inputs']
    if digest(source)!=identity['source_sha256'] or producer()!=identity['producer'] or o.file_hash(run/'MODELING.md')!=o.file_hash(GUIDE) or o.file_hash(run/'content-types.json')!=identity['content_types_sha256']:
        o.fail('Frozen source, implementation, humanizer or guide changed')
    units=verified_units(source,Path(identity['root']))
    directory=load_json(run/'document-map.json')
    if digest(directory)!=identity['document_map_sha256']:
        o.fail('Frozen document input changed')
    requirements=load_json(run/'requirements.json') if (run/'requirements.json').exists() else None
    requirements_hash=digest(requirements) if requirements is not None else None
    if requirements_hash!=identity['requirements_sha256']:
        o.fail('Frozen requirements changed')
    model=normalize(source,load_json(run/'draft.json'),directory,units)
    o.write(run/'candidates'/model['revision']/'organization.json',model)
    o.write(run/'candidate.json',model,mutable=True)
    template={'schema_version':o.REVIEW,'source_sha256':model['source_sha256'],'model_revision':model['revision'],
              'guide_sha256':model['guide_sha256'],'reviewer':'unknown','reviewer_type':'ai','model':'unknown',
              'checked_at':'unknown','method':'Compare original text, every directory boundary, claim, relation and frozen requirement',
              'requirements_sha256':requirements_hash,
              'judgments':[{**u,'verdict':'pending','reason':'Not reviewed'} for u in review_units(model,requirements)]}
    o.write(run/'review-template.json',template,mutable=True)
    issues=[]
    review=load_json(run/'review.json') if (run/'review.json').exists() else None
    if review is None:issues.append({'kind':'review','reason':'Independent review is required'})
    else:
        o.schema(review,'semantic-discourse-review-v1.schema.json')
        for key,expected in [('source_sha256',model['source_sha256']),('model_revision',model['revision']),('guide_sha256',model['guide_sha256']),('requirements_sha256',requirements_hash)]:
            if review.get(key)!=expected:issues.append({'kind':'dependency','reason':f'Review has stale {key}'})
        if review['reviewer'] in {'unknown',model['author']} or review['checked_at']=='unknown':
            issues.append({'kind':'review','reason':'Actual independent reviewer and time required'})
        entries=review['judgments']
        if len({x['target_id'] for x in entries})!=len(entries):o.fail('Duplicate review target')
        judgments={x['target_id']:x for x in entries}
        units_for_review=review_units(model,requirements)
        if set(judgments)!={u['target_id'] for u in units_for_review}:
            issues.append({'kind':'coverage','reason':'Review must cover each directory boundary, structure, relation and frozen requirement'})
        for unit in units_for_review:
            item=judgments.get(unit['target_id'],{})
            if item.get('content_sha256')!=unit['content_sha256']:
                issues.append({'kind':'dependency','target_id':unit['target_id'],'reason':'Reviewed content differs'})
            elif item.get('verdict')!='pass':
                issues.append({'kind':'semantic_review','target_id':unit['target_id'],'reason':item.get('reason','Not reviewed')})
    quality={'schema_version':'semantic-discourse-quality/v1','model_revision':model['revision'],
             'source_sha256':digest(source),'review_sha256':digest(review),'scope':package['scope'],'status':'pass' if not issues else 'fail',
             'requirements_sha256':requirements_hash,'program_checks':{'source_references':'pass','membership_coverage':'pass','relation_forms':'pass','qualifier_bindings':'pass','rollup_provenance':'pass','document_snapshot':'pass'},
             'semantic_review':'pass' if not issues else 'not_passed','issues':issues,
             'limits':'Program checks do not prove semantic faithfulness, readability, scientific validity or suitability.'}
    o.write(run/'assessments'/(digest(quality)+'.json'),quality)
    o.write(run/'quality.json',quality,mutable=True)
    return quality


def derived(source:dict,model:dict,package:dict)->dict[str,Any]:
    """Extend the v3 directory/occurrence projection with v4 semantics."""
    legacy=deepcopy(model)
    legacy['schema_version']='semantic-discourse-model/v3'
    legacy['edges']=[]
    legacy['blocks']=[{k:v for k,v in b.items() if k in {'id','parent_id','title','order','element_ids','related_element_ids'}} for b in model['blocks']]
    artifacts=v3.derived(source,legacy,package)
    domain=artifacts['model.json']
    reading_map=artifacts['reading-map.json']
    evidence=artifacts['evidence.json']['sources']
    elements,relations,_=o.source_index(source)
    objects={**elements,**relations}
    registry=load_json(TAXONOMY)
    domain['schema_version']='diagram-domain-model/v5'
    domain['content_types']={**registry,'sha256':o.file_hash(TAXONOMY)}
    def evidence_for(ident:str)->str:
        key='source:'+ident
        evidence_id='evidence:'+digest(key)[:24]
        if evidence_id not in evidence:
            obj=objects[ident]
            excerpt=obj.get('text',obj.get('note',''))
            anchors=obj.get('anchors',[])
            location='、'.join(dict.fromkeys(a['locator'] for a in anchors))
            path='evidence/'+digest(key)[:24]+'.md'
            evidence[evidence_id]={'path':path,'label':excerpt[:90] or ident,'excerpt':excerpt[:90] or ident,'location':location}
            artifacts[path]=f'# {ident}\n\n{excerpt}\n\n修订：{obj["content_revision"]}\n'
        return evidence_id
    def resolve_ref(ref:dict)->dict:
        obj=objects[ref['object_id']]
        return {**ref,'source':evidence_for(ref['object_id']),'value':o.pointer(obj,ref['field_path'])}
    def project_qualifiers(bindings:list[dict])->list[dict]:
        return [{**b,'source_ref':resolve_ref(b['source_ref'])} for b in bindings]
    blocks={b['id']:b for b in model['blocks']}
    for node in domain['nodes']:
        if node['id'] in blocks:
            block=blocks[node['id']]
            node['content_types']=block['content_types']
            node['focus_refs']=[resolve_ref(ref) for ref in block['focus_refs']]
            if block.get('focus_rationale'):node['focus_rationale']=block['focus_rationale']
    bindings={b['source_relation_id']:b['qualifiers'] for b in model['relation_bindings']}
    for edge in domain['edges']:
        rid=edge['id'].removeprefix('relation:')
        edge['relation_form']='source_atomic'
        edge['type']=relations[rid]['type']
        edge['qualifiers']=project_qualifiers(bindings.get(rid,[]))
    def org_evidence(edge:dict)->str:
        key='organization:'+edge['id']
        ident='evidence:'+digest(key)[:24]
        path='evidence/'+digest(key)[:24]+'.md'
        body=json.dumps(edge,ensure_ascii=False,indent=2,sort_keys=True)
        evidence[ident]={'path':path,'label':edge['id'],'excerpt':edge['id'],'location':''}
        artifacts[path]=f'# {edge["id"]}\n\n{body}\n'
        return ident
    for item in model['edges']:
        edge={'id':item['id'],'relation_form':item['relation_form'],'type':item['type'],
              'label':LABELS[item['type']],'from':item['from_id'],'to':item['to_id'],
              'from_element_ids':['element:'+x for x in item['from_element_ids']],
              'to_element_ids':['element:'+x for x in item['to_element_ids']],
              'direction':item['direction'],'origin':item['origin'],'basis':item['basis'],
              'premises':[resolve_ref(p) for p in item['premises']],
              'qualifiers':project_qualifiers(item.get('qualifiers',[])),
              'scope_note':item.get('scope_note','')}
        for key in ('overall_effect','rationale','uncertainty'):
            if item.get(key):edge[key]=item[key]
        if item['relation_form']=='source_rollup':
            edge['supporting_edge_ids']=['relation:'+x for x in item['source_relation_ids']]
            pairs=[]
            for rid in item['source_relation_ids']:
                relation=relations[rid]
                a,b=relation['from_id'],relation['to_id']
                if item['direction']=='symmetric' and a not in item['from_element_ids'] and b in item['from_element_ids']:
                    a,b=b,a
                pairs.append({'edge_id':'relation:'+rid,'from':'element:'+a,'to':'element:'+b,
                              'type':relation['type'],'direction':item['direction'],
                              'origin':'source','basis':relation['basis'],
                              'qualifiers':project_qualifiers(bindings.get(rid,[]))})
            edge['endpoint_pairs']=pairs
        edge['evidence']=[org_evidence(item),*dict.fromkeys(p['source'] for p in edge['premises'])]
        domain['edges'].append(edge)
        reading_map['edge_routes'].append({'edge_id':edge['id'],'from_id':edge['from'],'to_id':edge['to']})
    model_sha=sha256(o.json_bytes(domain)).hexdigest()
    reading_map['model_sha256']=model_sha
    reading_map['source_release']['model_sha256']=model_sha
    reading_map['source_release']['organization_sha256']=sha256(o.json_bytes(model)).hexdigest()
    artifacts['model.json']=domain
    artifacts['reading-map.json']=reading_map
    return artifacts


def reuse_review(run:Path,previous_review:Path)->dict:
    run=run.resolve();model=load_json(run/'candidate.json');prior=load_json(previous_review)
    o.schema(prior,'semantic-discourse-review-v1.schema.json')
    if prior['source_sha256']!=model['source_sha256'] or prior['guide_sha256']!=model['guide_sha256'] or prior['reviewer']=='unknown' or prior['checked_at']=='unknown':
        o.fail('Prior review needs actual reviewer and identical source and guide')
    requirements=load_json(run/'requirements.json') if (run/'requirements.json').exists() else None
    if prior.get('requirements_sha256')!=(digest(requirements) if requirements is not None else None):
        o.fail('Prior review uses different frozen requirements')
    template=load_json(run/'review-template.json')
    if template['model_revision']!=model['revision']:o.fail('Assess current draft first')
    judgments={(x['target_id'],x['content_sha256']):x for x in prior['judgments'] if x['verdict']=='pass'}
    reused=[]
    for item in template['judgments']:
        earlier=judgments.get((item['target_id'],item['content_sha256']))
        if earlier is not None:
            item.update(verdict='pass',reason=earlier['reason']);reused.append(item['target_id'])
    template['method']='Independently check pending units; explicitly carry forward exact unchanged prior judgments'
    o.write(run/'reused-review.json',prior)
    o.write(run/'review-reuse-template.json',template,mutable=True)
    o.write(run/'review-reuse-provenance.json',{'prior_review_sha256':digest(prior),
        'prior_model_revision':prior['model_revision'],'prior_guide_sha256':prior['guide_sha256'],
        'reused_target_ids':reused},mutable=True)
    return {'template':str(run/'review-reuse-template.json'),'reused_target_ids':reused}


def publish(run:Path)->dict:
    run=run.resolve();quality=assess(run)
    if quality['status']!='pass':o.fail('Independent semantic assessment has not passed; publication refused')
    model,source,review=(load_json(run/name) for name in ('candidate.json','source.json','review.json'))
    package=load_json(run/'work-package.json')
    payload={'organization.json':model,'source.json':source,'document-map.json':load_json(run/'document-map.json'),
             'content-types.json':(run/'content-types.json').read_text(),
             'work-package.json':package,'draft.json':load_json(run/'draft.json'),
             'review.json':review,'quality.json':quality,'MODELING.md':GUIDE.read_text()}
    for name in ('requirements.json','reused-review.json','review-reuse-provenance.json'):
        if (run/name).exists():payload[name]=load_json(run/name)
    for p in sorted(HUMANIZER.rglob('*')):
        if p.is_file():payload[str(p.relative_to(o.RESOURCES))]=p.read_text()
    revision=digest({'model':model['revision'],'review':review,'quality':quality,'producer':producer()})
    out=run/'releases'/revision;existed=out.exists()
    payload.update(derived(source,model,package))
    for name,value in payload.items():o.write(out/name,value)
    manifest={'schema_version':o.RUN,'revision':revision,'producer':producer(),
              'source_sha256':digest(source),'artifacts':{name:o.file_hash(out/name) for name in sorted(payload)}}
    o.write(out/'manifest.json',manifest)
    qrun,qout=shlex.quote(str(run)),shlex.quote(str(out))
    query_id=(model['edges'][0]['id'] if model['edges'] else
              'relation:'+source['relations'][0]['id'] if source['relations'] else model['root_id'])
    o.write(run/'RUNBOOK.md','# 篇章组织 v4 确切命令\n\n作者准备工作区并编辑草稿；独立语义审阅者按待审模板填写 review.json。绘图 review 是另一次真实输入核查，不代替语义审阅。\n\n```sh\n'
            +package['prepare_command']+'\n'
            +f'skm organize assess {qrun}\nskm organize publish {qrun}\n'
            +f'skm organize reuse-review {qrun} --from-review {shlex.quote(str(out/"review.json"))}\n'
            +f'skm organize query {qout} --id {shlex.quote(query_id)} --output {shlex.quote(str(run/"query-example.json"))}\n'
            +f'skm organize replay {shlex.quote(str(out/"manifest.json"))} --output-dir {shlex.quote(str(run.parent/(run.name+"-replay")))}\n'
            +'WB_REVIEWER=${WB_REVIEWER:?set actual drawing reviewer identity}\n'
            +'WB_NOTE=${WB_NOTE:?set actual input-check note}\n'
            +f'diagram-workbench review --project {qout} --reviewer "$WB_REVIEWER" --decision updated --note "$WB_NOTE"\n'
            +f'diagram-workbench build --project {qout}\ndiagram-workbench check --project {qout}\ndiagram-workbench preview --project {qout}\n```\n',mutable=True)
    return {'release':str(out),'manifest':str(out/'manifest.json'),'revision':revision,'reused':existed}


def replay(manifest_path:Path,output_dir:Path)->dict:
    manifest=load_json(manifest_path)
    if manifest.get('schema_version')!=o.RUN or manifest['producer']!=producer():
        o.fail('Replay needs exact installed organization implementation, Humanizer and guide')
    parent=manifest_path.resolve().parent
    for name,expected in manifest['artifacts'].items():
        p=(parent/name).resolve()
        if not p.is_relative_to(parent) or not p.is_file() or o.file_hash(p)!=expected:
            o.fail(f'Replay artifact missing or changed: {name}')
    output_dir=output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not (output_dir/'work-package.json').exists():
        o.fail('Replay target must be empty or same frozen workspace')
    for name in ('source.json','document-map.json','content-types.json','work-package.json','draft.json','review.json','MODELING.md',
                 'requirements.json','reused-review.json','review-reuse-provenance.json'):
        if name in manifest['artifacts']:
            value=(parent/name).read_text() if name in {'MODELING.md','content-types.json'} else load_json(parent/name)
            o.write(output_dir/name,value)
    result=publish(output_dir)
    if load_json(Path(result['manifest']))!=manifest:o.fail('Rebuilt release differs from frozen manifest')
    return {**result,'replayed_artifacts':len(manifest['artifacts']),'new_review_events':0}


def query(release:Path,ident:str)->dict:
    """Read a verified published object without widening its semantic scope."""
    parent=release.resolve().parent if release.is_file() else release.resolve()
    manifest=load_json(parent/'manifest.json')
    if manifest.get('producer')!=producer() or manifest.get('schema_version')!=o.RUN:
        o.fail('Query requires an exact v4 published release')
    for name,expected in manifest['artifacts'].items():
        path=(parent/name).resolve()
        if not path.is_relative_to(parent) or not path.is_file() or o.file_hash(path)!=expected:
            o.fail(f'Published release artifact changed: {name}')
    domain=load_json(parent/'model.json')
    model=load_json(parent/'organization.json')
    if domain.get('schema_version')!='diagram-domain-model/v5' or model.get('revision') is None:
        o.fail('Published graph or organization format differs')
    objects=[*domain['nodes'],*domain['edges']]
    matches=[x for x in objects if x['id']==ident]
    if not matches and not ident.startswith(('element:','relation:')):
        matches=[x for x in objects if x['id'] in {'element:'+ident,'relation:'+ident}]
    if len(matches)!=1:o.fail(f'Unknown or ambiguous published object ID: {ident}')
    item=matches[0]
    return {'schema_version':'semantic-discourse-query/v1','release_revision':manifest['revision'],
            'model_revision':model['revision'],'object':item}
