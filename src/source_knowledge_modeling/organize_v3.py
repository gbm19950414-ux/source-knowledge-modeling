"""Minimal, reviewed document reading organization over source export v4."""
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

VERSION = 3
GUIDE = o.RESOURCES / 'templates' / 'discourse-modeling-v3.md'
HUMANIZER = o.RESOURCES / 'resources' / 'humanizer'


def resource_hashes() -> dict[str, str]:
    return {str(p.relative_to(o.RESOURCES)): o.file_hash(p) for p in sorted(HUMANIZER.rglob('*')) if p.is_file()}


def guide_hash() -> str:
    return digest({'guide_file_sha256':o.file_hash(GUIDE),'humanizer_sha256':digest(resource_hashes())})


def producer() -> dict:
    names = [o.RESOURCES / 'organize_v3.py', o.RESOURCES / 'organize.py',
             o.RESOURCES / 'cli.py', GUIDE,
             o.RESOURCES / 'schemas' / 'semantic-discourse-draft-v3.schema.json',
             o.RESOURCES / 'schemas' / 'semantic-discourse-model-v3.schema.json']
    return {'name': 'source-knowledge-modeling', 'component': 'organize/v3',
            'implementation_sha256': digest({str(p.relative_to(o.RESOURCES)): o.file_hash(p) for p in names}),
            'guide_sha256': guide_hash(), 'humanizer_sha256': digest(resource_hashes())}


def verified_units(source: dict, root: Path) -> list[dict]:
    """Read and verify the exact source and snapshot bytes before using text."""
    record = source['source']
    root = root.resolve()
    raw = {}
    for key, hash_key in (('path', 'sha256'), ('snapshot_path', 'snapshot_sha256')):
        path = (root / record[key]).resolve()
        if not path.is_relative_to(root):
            o.fail('Source path escapes root')
        try:
            raw[key] = path.read_bytes()
        except OSError as exc:
            o.fail(f'Missing source file: {path}: {exc}')
        if sha256(raw[key]).hexdigest() != record[hash_key]:
            o.fail(f'Source hash mismatch: {record[key]}')
    try:
        snapshot = json.loads(raw['snapshot_path'])
        units = snapshot['units']
    except (ValueError, KeyError, TypeError):
        o.fail('Invalid frozen source snapshot')
    if not isinstance(units, list) or not all(isinstance(u, dict) and isinstance(u.get('locator'), str) and isinstance(u.get('text'), str) for u in units):
        o.fail('Snapshot units require locator and text')
    if len({u['locator'] for u in units}) != len(units):
        o.fail('Repeated snapshot locator')
    exported = [u['locator'] for u in source['source_units']]
    if not set(exported) <= {u['locator'] for u in units}:
        o.fail('Export source unit absent from snapshot')
    return units


def directory_template(source: dict, units: list[dict]) -> dict:
    selected = {u['locator']: u for u in units}
    paragraphs = [{'id': u['locator'], 'locator': u['locator'], 'order': i,
                   'text': selected[u['locator']]['text'], 'evidence': []}
                  for i, u in enumerate(source['source_units'])]
    return {'schema_version': 'source-reading-map/v2', 'title': source['source']['title'],
            'root_id': 'chapter:document',
            'containers': [{'id': 'chapter:document', 'parent_id': None, 'kind': 'chapter',
                            'title': source['source']['title'], 'order': 0, 'evidence': []}],
            'paragraphs': paragraphs, 'verification': 'pending'}


def extract_directory(value: dict, source: dict, units: list[dict], *, supplied: bool) -> dict:
    if value.get('schema_version') not in {'source-reading-map/v1', 'source-reading-map/v2'}:
        o.fail('Document map must be an existing source-reading-map/v1 or v2')
    snapshot = {u['locator']: u for u in units}
    paragraphs = value.get('paragraphs')
    if not isinstance(paragraphs, list) or not paragraphs:
        o.fail('Document map needs paragraphs')
    ids = set()
    for paragraph in paragraphs:
        if not {'id', 'locator', 'order', 'text'} <= paragraph.keys():
            o.fail('Document paragraph identity/order/text missing')
        if paragraph['id'] in ids or paragraph['locator'] not in snapshot or paragraph['text'] != snapshot[paragraph['locator']]['text']:
            o.fail('Document paragraph differs from frozen snapshot')
        ids.add(paragraph['id'])
        if 'continues_from' in paragraph and paragraph['continues_from'] not in ids:
            o.fail('Document paragraph continuation must name earlier paragraph')
    snapshot_rank={u['locator']:i for i,u in enumerate(units)}
    if ([p['order'] for p in paragraphs] != sorted(p['order'] for p in paragraphs)
            or len({p['order'] for p in paragraphs}) != len(paragraphs)
            or [snapshot_rank[p['locator']] for p in paragraphs] != sorted(snapshot_rank[p['locator']] for p in paragraphs)):
        o.fail('Document paragraphs require unique original source order')
    containers = [{k: deepcopy(v) for k, v in item.items() if k in {'id','parent_id','kind','title','order','range'}}
                  for item in value.get('containers', []) if item.get('kind') in {'chapter','section'}]
    if not containers or len({c['id'] for c in containers}) != len(containers):
        o.fail('Document map needs unique chapter/section containers')
    by_id = {c['id']: c for c in containers}
    if value.get('root_id') not in by_id or by_id[value['root_id']]['kind'] != 'chapter':
        o.fail('Document root must name a chapter')
    ranks = {p['id']: i for i,p in enumerate(paragraphs)}
    for c in containers:
        if not {'id','parent_id','kind','title','order'} <= c.keys() or not isinstance(c['title'],str) or not c['title'].strip():
            o.fail('Document heading needs title, parent and order')
        if c['kind'] == 'chapter':
            if c['parent_id'] is not None:
                o.fail('Chapter must be root')
        elif c['parent_id'] not in by_id:
            o.fail('Section must have chapter/section parent')
        if 'range' in c:
            start,end = c['range'].get('start',{}),c['range'].get('end',{})
            if start.get('paragraph_id') not in ranks or end.get('paragraph_id') not in ranks:
                o.fail('Section range has absent paragraph')
            a,b = ranks[start['paragraph_id']],ranks[end['paragraph_id']]
            if a>b or any(type(x.get('offset')) is not int or x['offset']<0 or x['offset']>len(paragraphs[ranks[x['paragraph_id']]]['text']) for x in (start,end)):
                o.fail('Invalid document range')
        elif c['kind'] == 'section':
            o.fail('Section needs original range')
    for c in containers:
        seen={c['id']}; parent=c['parent_id']
        while parent is not None:
            if parent in seen or parent not in by_id:
                o.fail('Document container cycle')
            seen.add(parent);parent=by_id[parent]['parent_id']
        if c['kind']=='section' and value['root_id'] not in seen:
            o.fail('Section outside root')
    siblings={}
    for c in containers:
        siblings.setdefault(c['parent_id'],[]).append(c)
    if any(len({c['order'] for c in items})!=len(items) for items in siblings.values()):
        o.fail('Sibling heading orders must be unique')
    def endpoints(c):
        r=c.get('range')
        return None if r is None else ((ranks[r['start']['paragraph_id']],r['start']['offset']),
                                       (ranks[r['end']['paragraph_id']],r['end']['offset']))
    for c in containers:
        own=endpoints(c);parent=by_id.get(c['parent_id'])
        outer=endpoints(parent) if parent else None
        if own and outer and not (outer[0]<=own[0]<own[1]<=outer[1]):
            o.fail('Section range escapes parent range')
    for items in siblings.values():
        ranged=sorted((endpoints(c) for c in items if endpoints(c)),key=lambda x:x[0])
        if any(a[1]>b[0] for a,b in zip(ranged,ranged[1:])):
            o.fail('Sibling section ranges overlap')
    # Prior object mappings, blocks, summaries and routes are never imported.
    return {'schema_version':'source-document-directory/v1', 'source_snapshot_sha256':source['source']['snapshot_sha256'],
            'root_id':value['root_id'], 'title':value['title'], 'containers':containers,
            'paragraphs':[{k:deepcopy(v) for k,v in p.items() if k in {'id','locator','order','text','continues_from'}} for p in paragraphs],
            'verification':'verified' if supplied else 'pending'}


def verify_directory(value: dict, source: dict, units: list[dict]) -> dict:
    if value.get('schema_version') != 'source-document-directory/v1' or value.get('source_snapshot_sha256') != source['source']['snapshot_sha256']:
        o.fail('Directory is not bound to frozen snapshot')
    # Re-run paragraph, title, parent, order and range checks after edits.
    return extract_directory({**value, 'schema_version':'source-reading-map/v2'},source,units,supplied=value.get('verification')=='verified')

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
                'producer': producer()}
    run = output_dir.resolve()
    if (run / 'work-package.json').exists():
        package = load_json(run / 'work-package.json')
        if package['inputs'] != identity or digest(load_json(run / 'source.json')) != identity['source_sha256'] or o.file_hash(run / 'MODELING.md') != o.file_hash(GUIDE):
            o.fail('Workspace inputs/guide changed; prepare a new directory')
        return {'run_dir': str(run), 'reused': True, 'input_fingerprint': digest(identity)}
    if run.exists() and any(run.iterdir()):
        o.fail('New workspace must be empty')
    draft = {'schema_version': 'semantic-discourse-draft/v3', 'source_sha256': digest(source), 'author': 'unknown',
             'directory_status': directory['verification'], 'root_id': directory['root_id'], 'containers': directory['containers'],
             'blocks': [], 'edges': [], 'reading_structures': []}
    for name, value in [('source.json', source), ('document-map.json', directory), ('draft.json', draft),
                        ('MODELING.md', GUIDE.read_text()), ('work-package.json', {
                            'schema_version': 'semantic-discourse-work-package/v1', 'inputs': identity,
                            'input_fingerprint': digest(identity), 'scope': scope_report,
                            'requirements': 'Review every chapter, section, relation and frozen requirement independently.'})]:
        o.write(run / name, value)
    if requirements is not None:
        o.write(run / 'requirements.json', requirements)
    qrun = shlex.quote(str(run))
    o.write(run / 'RUNBOOK.md', '# 篇章组织 v3\n\n核对 document-map.json，编辑 draft.json；assess 产生待审模板，不产生批准。\n\n```sh\n'
            + f'skm organize prepare {shlex.quote(str(export_path.resolve()))} --root {shlex.quote(str(root.resolve()))} --output-dir {qrun} --format-version 3'
            + (f' --document-map {shlex.quote(str(document_map_path.resolve()))}' if document_map_path else '')
            + (f' --requirements {shlex.quote(str(requirements_path.resolve()))}' if requirements_path else '')
            + f'\nskm organize assess {qrun}\nskm organize publish {qrun}\n```\n')
    return {'run_dir': str(run), 'reused': False, 'input_fingerprint': digest(identity),
            'directory_status': draft['directory_status'], 'scope': scope_report}

LABELS = {'conditions':'条件','exception_to':'例外','replaces':'替代','supports':'支持',
          'precedes':'先于','quantifies':'量化','example_of':'示例','qualifies':'限定',
          'causes':'导致','contrasts_with':'对照','elaborates':'阐释'}


def _refs(values: list[dict], objects: dict[str,dict]) -> list[dict]:
    if not isinstance(values,list):
        o.fail('Premises must be source field references')
    result=[]
    for ref in values:
        obj=objects.get(ref.get('object_id'))
        if obj is None or not ref.get('field_path','').startswith('/') or ref['field_path'].split('/')[1] not in {'text','quantity','modality','negated','example_scope','type','note','rationale','uncertainty','from_id','to_id'}:
            o.fail('Premise must name an existing meaning field')
        o.pointer(obj,ref['field_path'])
        if ref.get('content_revision',obj['content_revision'])!=obj['content_revision']:
            o.fail('Premise has stale source revision')
        result.append({'object_id':obj['id'],'content_revision':obj['content_revision'],'field_path':ref['field_path']})
    if len({digest(x) for x in result})!=len(result):
        o.fail('Repeated premise')
    return result


def _members(model: dict, elements: dict, source: dict) -> tuple[dict[str,set[str]],dict[str,str],dict[str,list[str]]]:
    directory=model['containers']; containers=o.index(directory,'document container'); blocks=o.index(model['blocks'],'block')
    if model['root_id'] not in containers or containers[model['root_id']]['kind']!='chapter':
        o.fail('Root must be an original chapter')
    parents={c['id']:c['parent_id'] for c in directory}
    for block in blocks.values():
        if block['parent_id'] not in containers or block['parent_id']==model['root_id'] and containers[block['parent_id']]['kind']!='chapter':
            o.fail('Block needs chapter/section parent')
        for eid in block.get('element_ids',[]):
            if eid not in elements:
                o.fail('Block references absent element')
        if len(block.get('element_ids',[]))!=len(set(block.get('element_ids',[]))):
            o.fail('Repeated block element')
        related=block.get('related_element_ids',[])
        if (len(related)!=len(set(related)) or not set(related)<=set(elements)
                or set(related)&set(block.get('element_ids',[]))):
            o.fail('Related block references need distinct existing non-owned elements')
        parents[block['id']]=block['parent_id']
    paragraphs={p['locator']:p for p in model['paragraphs']}
    rank={p['id']:i for i,p in enumerate(model['paragraphs'])}
    def contains(c:dict, loc:str, start:int, end:int)->bool:
        if loc not in paragraphs: return False
        if 'range' not in c: return True
        r=c['range']; a,b=r['start'],r['end']; i=rank[paragraphs[loc]['id']]
        return (rank[a['paragraph_id']],a['offset']) <= (i,start) and (i,end) <= (rank[b['paragraph_id']],b['offset'])
    placements={ident:[] for ident in elements}
    occurrence_parent={}
    for occurrence in source['occurrences']:
        loc=occurrence['locator']; start,end=occurrence['start'],occurrence['end']
        if loc not in paragraphs:
            continue
        candidates=[c for c in directory if c['kind']=='section' and contains(c,loc,start,end)]
        if not candidates:
            # An explicit chapter range owns its preamble and other material
            # outside sections. A range-less chapter in a selected-section map
            # must not silently expand that selection to the entire export.
            has_sections=any(c['kind']=='section' for c in directory)
            candidates=[c for c in directory if c['kind']=='chapter'
                        and ('range' in c or not has_sections)
                        and contains(c,loc,start,end)]
        if not candidates:
            continue
        def depth(c):
            n=0;p=c['parent_id']
            while p is not None: n+=1;p=parents[p]
            return n
        deepest=max(depth(c) for c in candidates)
        exact=[c for c in candidates if depth(c)==deepest]
        if len(exact)!=1:o.fail('Source occurrence has ambiguous sibling section scope')
        parent=exact[0]['id']
        occurrence_parent[occurrence['id']]=parent
        placements[occurrence['element_id']].append(parent)
    members={ident:set() for ident in parents}
    for eid, scopes in placements.items():
        for scope in scopes:
            p=scope
            while p is not None:
                members[p].add(eid);p=parents[p]
    for block in blocks.values():
        if not set(block.get('element_ids',[])) <= members[block['parent_id']]:
            o.fail('Block claims element outside its document scope')
        members[block['id']]=set(block.get('element_ids',[]))
    selected={eid for eid,scopes in placements.items() if scopes}
    for block in blocks.values():
        if not set(block.get('related_element_ids',[]))<=selected:
            o.fail('Related block element must already occur in selected source scope')
    owned=set()
    for block in blocks.values():
        for eid in block.get('element_ids',[]):
            key=(block['parent_id'],eid)
            if key in owned:o.fail('Multiple blocks own the same document-scope element')
            owned.add(key)
    return members,occurrence_parent,parents


def normalize(source:dict,draft:dict,directory:dict,units:list[dict])->dict:
    o.schema(draft,'semantic-discourse-draft-v3.schema.json')
    elements,relations,_=o.source_index(source)
    objects={**elements,**relations}
    if draft['source_sha256']!=digest(source) or draft['author'].strip()=='unknown':
        o.fail('Draft source or actual author identity missing')
    derived_directory=verify_directory({**directory,'containers':draft['containers']},source,units)
    if draft['root_id']!=derived_directory['root_id']:
        o.fail('Draft root differs from frozen directory')
    if draft['directory_status'] not in {'pending','verified'}:
        o.fail('Invalid document directory status')
    if directory['verification']=='verified' and draft['directory_status']!='verified':
        o.fail('Verified input directory may not be downgraded')
    result=deepcopy(draft)
    result['paragraphs']=deepcopy(directory['paragraphs'])
    result['containers']=derived_directory['containers']
    reserved=set(objects)|{'element:'+x for x in elements}|{'relation:'+x for x in relations}
    nodes=o.index(result['containers']+result['blocks'],'organization node')
    if set(nodes)&reserved:
        o.fail('Organization node collides with source identity')
    members,occurrence_parent,parents=_members(result,elements,source)
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
        if not edge['premises']:
            o.fail('Higher edge needs source premises')
        support=edge.get('source_relation_ids',[])
        if len(support)!=len(set(support)) or not set(support)<=set(relations):
            o.fail('Invalid supporting relation')
        if edge['origin']=='source':
            if not support or not edge.get('scope_note','').strip():
                o.fail('Source rollup needs exact relations and scope')
            for rid in support:
                relation=relations[rid]
                if relation['type']!=edge['type'] or relation['origin_type'] not in {'source_claim','source_example'}:
                    o.fail('Source rollup changes source relation type or identity')
                a,b=relation['from_id'],relation['to_id']
                if relation['type']=='contrasts_with' and a not in edge['from_element_ids'] and b in edge['from_element_ids']:
                    a,b=b,a
                if {a}!=set(edge['from_element_ids']) or {b}!=set(edge['to_element_ids']):
                    o.fail('Source rollup endpoint membership differs from source edge')
        elif edge['origin']=='interpretation':
            if support or not edge.get('rationale','').strip():
                o.fail('Interpretation needs rationale, not source rollup support')
        else:o.fail('Invalid higher edge origin')
    edges=o.index(result['edges'],'organization edge')
    if set(edges)&(set(nodes)|reserved):o.fail('Higher edge identity collision')
    structures=o.index(result['reading_structures'],'reading structure')
    if set(structures)&(set(edges)|set(nodes)|reserved):o.fail('Reading structure identity collision')
    if len({x['scope_id'] for x in structures.values()})!=len(structures):o.fail('Repeated reading structure scope')
    for item in structures.values():
        scope=item['scope_id']
        if scope not in nodes:o.fail('Reading structure scope absent')
        universe={x['id'] for x in result['containers']+result['blocks'] if x.get('parent_id')==scope}
        owned={e for b in result['blocks'] if b['parent_id']==scope for e in b.get('element_ids',[])}
        universe|={occ['element_id'] for occ in source['occurrences']
                   if occ['id'] in occurrence_parent and occurrence_parent[occ['id']]==scope and occ['element_id'] not in owned}
        if scope in {b['id'] for b in result['blocks']}:
            universe|=set(nodes[scope].get('element_ids',[]))
        assigned=[*item.get('core_ids',[]),*(s['node_id'] for s in item.get('supports',[])),*item.get('independent_ids',[])]
        if assigned and not item.get('reading_order'):
            source_rank={eid:i for i,eid in enumerate(dict.fromkeys(occ['element_id'] for occ in source['occurrences']))}
            item['reading_order']=sorted(assigned,key=lambda ident:(nodes[ident]['order'],ident) if ident in nodes else (source_rank.get(ident,10**9),ident))
        if len(assigned)!=len(set(assigned)) or not set(assigned)<=universe or len(item.get('reading_order',[]))!=len(set(item.get('reading_order',[]))) or set(item.get('reading_order',[]))!=set(assigned):
            o.fail('Reading structure assigned members must be unique direct scope members')
        if any(s['anchor_id'] not in item.get('core_ids',[]) or s['node_id']==s['anchor_id'] for s in item.get('supports',[])):
            o.fail('Support needs distinct core anchor')
        if item['pattern'] in ('sequence','comparison') and len(item.get('core_ids',[]))<2 or item['pattern']=='explanation' and not item.get('core_ids',[]):
            o.fail('Reading pattern needs declared cores')
        item['refs']=_refs(item.get('refs',[]),objects)
    result['schema_version']='semantic-discourse-model/v3'
    result['source_revision']=source['artifact_revision_id']
    result['guide_sha256']=guide_hash()
    result['document_sha256']=digest(directory)
    result['revision']=digest(result)
    o.schema(result,'semantic-discourse-model-v3.schema.json')
    return result


def requirement_units(requirements:Any)->list[dict]:
    if requirements is None:return []
    if isinstance(requirements,dict) and isinstance(requirements.get('requirements'),list):
        groups=[('requirements',requirements['requirements'])]
    elif isinstance(requirements,dict):
        groups=[(key,value) for key,value in sorted(requirements.items())]
    else:groups=[('requirements',requirements)]
    result=[]
    for key,value in groups:
        entries=value if isinstance(value,list) else [value]
        for i,item in enumerate(entries):
            raw=item.get('id') if isinstance(item,dict) else None
            identifier=raw if isinstance(raw,str) and raw and all(c.isalnum() or c in '_.:-' for c in raw) else f'{key}:{i}'
            result.append({'target_id':'requirement:'+identifier,
                           'content_sha256':digest({'group':key,'item':item})})
    if len({x['target_id'] for x in result})!=len(result):o.fail('Duplicate frozen requirement ID')
    return result


def review_units(model:dict,requirements:Any)->list[dict]:
    units=[{'target_id':x['id'],'content_sha256':digest(x)} for x in [*model['containers'],*model['blocks'],*model['edges'],*model['reading_structures']]]
    units.append({'target_id':'directory:verification','content_sha256':digest({'directory_status':model['directory_status'],'document_sha256':model['document_sha256'],'containers':model['containers'],'paragraphs':model['paragraphs']})})
    units.extend(requirement_units(requirements))
    if len({u['target_id'] for u in units})!=len(units):o.fail('Review target identity collision')
    return units


def assess(run:Path)->dict:
    run=run.resolve()
    source=load_json(run/'source.json');package=load_json(run/'work-package.json')
    identity=package['inputs']
    if digest(source)!=identity['source_sha256'] or producer()!=identity['producer'] or o.file_hash(run/'MODELING.md')!=o.file_hash(GUIDE):
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
             'requirements_sha256':requirements_hash,'program_checks':{'source_references':'pass','membership_coverage':'pass','rollup_provenance':'pass','document_snapshot':'pass'},
             'semantic_review':'pass' if not issues else 'not_passed','issues':issues,
             'limits':'Program checks do not prove semantic faithfulness, readability, scientific validity or suitability.'}
    o.write(run/'assessments'/(digest(quality)+'.json'),quality)
    o.write(run/'quality.json',quality,mutable=True)
    return quality


def derived(source:dict,model:dict,package:dict)->dict[str,Any]:
    elements,relations,_=o.source_index(source)
    objects={**elements,**relations}
    members,occurrence_parent,parents=_members(model,elements,source)
    evidence={};artifacts={}
    def add(key:str,label:str,text:str,excerpt:str='',location:str='')->list[str]:
        ident='evidence:'+digest(key)[:24];path='evidence/'+digest(key)[:24]+'.md'
        evidence[ident]={'path':path,'label':label,'excerpt':excerpt or text,'location':location}
        artifacts[path]=f'# {label}\n\n{text}\n'
        return [ident]
    selected_ids={occ['element_id'] for occ in source['occurrences'] if occ['id'] in occurrence_parent}
    selected_relations={rid for rid,relation in relations.items()
                        if relation['from_id'] in selected_ids and relation['to_id'] in selected_ids}
    cited_ids={ref['object_id'] for item in [*model['edges'],*model['reading_structures']]
               for ref in item.get('premises',item.get('refs',[]))}
    def context_closure(ident:str)->list[str]:
        seen=set();pending=list(objects[ident].get('context_ids',[]))
        while pending:
            current=pending.pop(0)
            if current in seen:continue
            if current not in objects:o.fail(f'Absent explicit context object: {current}')
            seen.add(current);pending.extend(objects[current].get('context_ids',[]))
        return sorted(seen)
    visible_evidence_ids=selected_ids|selected_relations|cited_ids
    visible_evidence_ids|={context for ident in tuple(visible_evidence_ids) for context in context_closure(ident)}
    object_evidence={}
    for obj in [*elements.values(),*relations.values()]:
        if obj['id'] not in visible_evidence_ids:continue
        anchors=obj.get('anchors',[]);quotes='\n\n'.join(a['quote'] for a in anchors)
        locations='、'.join(dict.fromkeys(a['locator'] for a in anchors))
        canonical=obj.get('text',obj.get('note',''))
        body=canonical if canonical!=quotes else quotes
        object_evidence[obj['id']]=add('source:'+obj['id'],canonical[:90] or obj['id'],
                                       f'{body}\n\n修订：{obj["content_revision"]}' + (f'\n\n{quotes}' if quotes and quotes!=canonical else ''),
                                       (quotes or canonical)[:90],locations)
    def linked_evidence(ident:str)->list[str]:
        return list(dict.fromkeys(e for source_id in [ident,*context_closure(ident)]
                                  for e in object_evidence[source_id]))
    def organization_evidence(obj:dict)->list[str]:
        body=json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)
        return add('organization:'+obj['id'],obj.get('title',obj.get('type',obj['id'])),body)
    ordered=list(dict.fromkeys(x['element_id'] for x in source['occurrences'] if x['id'] in occurrence_parent))
    rank={eid:i for i,eid in enumerate(ordered)}
    nodes=[]
    containers=model['containers'];blocks=model['blocks']
    children={c['id']:[] for c in containers+blocks}
    for c in containers+blocks:
        if c.get('parent_id') is not None:children[c['parent_id']].append(c['id'])
    for occ in source['occurrences']:
        if occ['id'] not in occurrence_parent:continue
        ident='element:'+occ['element_id'];parent=occurrence_parent[occ['id']]
        if ident not in children[parent]:children[parent].append(ident)
    for b in blocks:
        for eid in b.get('element_ids',[]):
            ident='element:'+eid
            if ident in children[b['parent_id']]:children[b['parent_id']].remove(ident)
            if ident not in children[b['id']]:children[b['id']].append(ident)
    order={x['id']:x['order'] for x in containers+blocks}
    for nid in children:
        children[nid].sort(key=lambda x:(0,order[x],x) if x in order else (1,rank[x.removeprefix('element:')],x))
    for obj in containers+blocks:
        node={'id':obj['id'],'label':obj['title'],'kind':obj.get('kind','block'),
              'origin':'organization','order':obj['order'],'children':children[obj['id']],
              'evidence':organization_evidence(obj)}
        if obj.get('related_element_ids'):node['related_element_ids']=['element:'+x for x in obj['related_element_ids']]
        nodes.append(node)
    for eid in ordered:
        obj=elements[eid]
        nodes.append({'id':'element:'+eid,'label':obj['text'],'kind':obj['kind'],'order':rank[eid],
                      'origin':'interpretation' if obj['origin_type']=='project_interpretation' else 'source',
                      'source_revision':obj['content_revision'],'evidence':linked_evidence(eid)})
    edges=[]
    for obj in relations.values():
        if obj['from_id'] not in ordered or obj['to_id'] not in ordered:continue
        edges.append({'id':'relation:'+obj['id'],'type':obj['type'],'label':LABELS[obj['type']],
                      'from':'element:'+obj['from_id'],'to':'element:'+obj['to_id'],
                      'direction':'symmetric' if obj['type']=='contrasts_with' else 'directed',
                      'origin':'interpretation' if obj['origin_type']=='project_interpretation' else 'source',
                      'basis':obj['basis'],'details':obj.get('note',''),'rationale':obj['rationale'],
                      **({'uncertainty':obj['uncertainty']} if obj['uncertainty'] else {}),'premises':[{'object_id':p,'content_revision':objects[p]['content_revision'],
                      'field_path':'/text' if p in elements else '/note','source':object_evidence[p][0]} for p in obj['premises']],
                      'evidence':linked_evidence(obj['id'])})
    for obj in model['edges']:
        edges.append({'id':obj['id'],'type':obj['type'],'label':LABELS[obj['type']],
                      'from':obj['from_id'],'to':obj['to_id'],'from_element_ids':['element:'+x for x in obj['from_element_ids']],
                      'to_element_ids':['element:'+x for x in obj['to_element_ids']],
                      'direction':obj['direction'],'origin':obj['origin'],'basis':'inferred' if obj['origin']=='interpretation' else 'source_rollup',
                      'details':obj.get('rationale',''),'rationale':obj.get('rationale',''),
                      **({'uncertainty':obj['uncertainty']} if obj.get('uncertainty') else {}),
                      **({'scope_note':obj['scope_note']} if obj.get('scope_note') else {}),
                      'premises':[{**p,'source':object_evidence[p['object_id']][0]} for p in obj['premises']],'supporting_edge_ids':['relation:'+x for x in obj.get('source_relation_ids',[])],
                      'evidence':organization_evidence(obj)+list(dict.fromkeys(e for r in obj['premises'] for e in linked_evidence(r['object_id'])))})
    domain={'schema_version':'diagram-domain-model/v4','title':model['title'] if 'title' in model else next(x['title'] for x in containers if x['id']==model['root_id']),
            'root_id':model['root_id'],'nodes':nodes,'edges':edges,'reading_order':['element:'+x for x in ordered],
            'reading_structures':[]}
    for item in model['reading_structures']:
        convert=lambda x:'element:'+x if x in elements else x
        domain['reading_structures'].append({'id':item['id'],'scope_id':item['scope_id'],'pattern':item['pattern'],
            'core_ids':[convert(x) for x in item.get('core_ids',[])],
            'supports':[{**s,'node_id':convert(s['node_id']),'anchor_id':convert(s['anchor_id'])} for s in item.get('supports',[])],
            'independent_ids':[convert(x) for x in item.get('independent_ids',[])],
            'reading_order':[convert(x) for x in item.get('reading_order',[])],
            **({'rationale':item['rationale']} if item.get('rationale') else {}),
            'evidence':organization_evidence(item)+list(dict.fromkeys(e for r in item.get('refs',[]) for e in linked_evidence(r['object_id'])))})
    model_sha=sha256(o.json_bytes(domain)).hexdigest()
    # Native image units can have no transcription. Keep them in the frozen
    # document map and object region evidence, rather than inventing paragraph
    # text for the reading map. Textual spans/range endpoints must remain real.
    paragraph_ids={p['id'] for p in model['paragraphs'] if p['text']}
    for container in containers:
        if any(ref['paragraph_id'] not in paragraph_ids for ref in container.get('range',{}).values()):
            o.fail('Reading range endpoint needs a transcribed paragraph')
    paragraphs=[{**p,'evidence':add('paragraph:'+p['id'],p['id'],p['text'],p['text'][:90],p['locator'])}
                for p in model['paragraphs'] if p['text']]
    map_containers=[]
    for obj in containers+blocks:
        entry={'id':obj['id'],'parent_id':obj['parent_id'],'kind':obj.get('kind','block'),
               'title':obj['title'],'order':obj['order'],'evidence':organization_evidence(obj),
               'object_id':obj['id']}
        if 'range' in obj:entry['range']=obj['range']
        map_containers.append(entry)
    occurrences=[];merged={};source_spans={}
    paragraph_by_locator={p['locator']:p['id'] for p in paragraphs}
    for i,occ in enumerate(x for x in source['occurrences'] if x['id'] in occurrence_parent):
        parent=occurrence_parent[occ['id']]
        owning=[b['id'] for b in blocks if b['parent_id']==parent and occ['element_id'] in b.get('element_ids',[])]
        if len(owning)>1:o.fail('Multiple blocks own the same occurrence')
        effective_parent=owning[0] if owning else parent
        span={'paragraph_id':paragraph_by_locator[occ['locator']], 'start':occ['start'],'end':occ['end']}
        source_spans.setdefault(occ['element_id'],[]).append(span)
        key=(effective_parent,occ['element_id'])
        if key not in merged:
            merged[key]={'id':occ['id'],'parent_id':effective_parent,
                         'object_id':'element:'+occ['element_id'],'order':i,'spans':[]}
            occurrences.append(merged[key])
        merged[key]['spans'].append(span)
    next_order=max((x['order'] for x in occurrences),default=-1)+1
    for block in blocks:
        for eid in block.get('related_element_ids',[]):
            key=(block['id'],eid)
            if key in merged:o.fail('Related reference duplicates owned occurrence')
            reference={'id':'related:'+block['id']+':'+eid,'parent_id':block['id'],
                       'object_id':'element:'+eid,'order':next_order,
                       'spans':source_spans[eid]}
            next_order+=1
            merged[key]=reference;occurrences.append(reference)
    occurrence_ids={}
    for occurrence in occurrences:occurrence_ids.setdefault(occurrence['object_id'],occurrence['id'])
    routes=[]
    for edge in edges:
        if edge['from'].startswith('element:') and edge['to'].startswith('element:'):
            routes.append({'edge_id':edge['id'],'from_id':occurrence_ids[edge['from']],
                           'to_id':occurrence_ids[edge['to']]})
        else:
            routes.append({'edge_id':edge['id'],'from_id':edge['from'],'to_id':edge['to']})
    reading_map={'schema_version':'source-reading-map/v2','title':domain['title'],'model_sha256':model_sha,
                 'root_id':model['root_id'],'author':{'identity':model['author'],'model':'unknown'},
                 'source_release':{'artifact_revision_id':source['artifact_revision_id'],
                    'export_sha256':package['inputs']['source_file_sha256'],'model_sha256':model_sha,
                    'organization_sha256':sha256(o.json_bytes(model)).hexdigest(),
                    'transcription_sha256':source['source']['snapshot_sha256']},
                 'containers':map_containers,'occurrences':occurrences,'paragraphs':paragraphs,
                 'edge_routes':routes}
    artifacts.update({'model.json':domain,'reading-map.json':reading_map,'evidence.json':{'sources':evidence},
                      'diagram.config.json':{'schema':1,'target':'domain-model','model':'model.json','evidence':'evidence.json',
                                             'readingMap':'reading-map.json','presentation':'nested-source','review':'diagram-review.json',
                                             'output':'.diagrams','inputs':['organization.json','quality.json','review.json'],'watch':[]}})
    return artifacts


def reuse_review(run:Path,previous_review:Path)->dict:
    run=run.resolve();model=load_json(run/'candidate.json');prior=load_json(previous_review)
    o.schema(prior,'semantic-discourse-review-v1.schema.json')
    if prior['source_sha256']!=model['source_sha256'] or prior['reviewer']=='unknown' or prior['checked_at']=='unknown':
        o.fail('Prior review must identify actual reviewer and same source')
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
    o.write(run/'RUNBOOK.md','# 篇章组织 v3 确切命令\n\n编辑草稿后重新独立审阅。\n\n```sh\n'
            +f'skm organize assess {qrun}\nskm organize publish {qrun}\n'
            +f'skm organize replay {shlex.quote(str(out/"manifest.json"))} --output-dir {shlex.quote(str(run.parent/(run.name+"-replay")))}\n'
            +f'diagram-workbench review --project {qout}\ndiagram-workbench build --project {qout}\ndiagram-workbench check --project {qout}\ndiagram-workbench preview --project {qout}\n```\n',mutable=True)
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
    for name in ('source.json','document-map.json','work-package.json','draft.json','review.json','MODELING.md',
                 'requirements.json','reused-review.json','review-reuse-provenance.json'):
        if name in manifest['artifacts']:
            value=(parent/name).read_text() if name=='MODELING.md' else load_json(parent/name)
            o.write(output_dir/name,value)
    result=publish(output_dir)
    if load_json(Path(result['manifest']))!=manifest:o.fail('Rebuilt release differs from frozen manifest')
    return {**result,'replayed_artifacts':len(manifest['artifacts']),'new_review_events':0}
