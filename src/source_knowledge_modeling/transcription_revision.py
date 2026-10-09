"""Declarative original-source corrections create a new snapshot and inventory.

This is a transcription boundary: it never invents semantic acceptance or edits
an old snapshot, and it retains the original parser output as immutable history.
"""
from copy import deepcopy
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from .core import KnowledgeFoundationError, digest_bytes, digest_text, load_json, validate
from .semantic_v3 import digest


def _bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()


def revise_transcription(foundation_path: Path, *, root: Path, corrections_path: Path, output_dir: Path):
    root=root.resolve();out=output_dir.resolve();base=load_json(foundation_path);patch=load_json(corrections_path)
    validate(base,root=root,resolve=True)
    schema=load_json(Path(__file__).parent/'schemas/knowledge-transcription-corrections-v1.schema.json')
    errors=list(Draft202012Validator(schema).iter_errors(patch))
    if errors:raise KnowledgeFoundationError('Transcription corrections: '+errors[0].message)
    if patch['source_sha256']!=base['source']['sha256'] or patch['snapshot_sha256']!=base['source']['snapshot_sha256']:
        raise KnowledgeFoundationError('Correction source/snapshot identity is stale')
    if base['source']['kind']=='pdf' and patch['check_method']!='original_visual':
        raise KnowledgeFoundationError('PDF transcription correction requires actual original-page verification')
    try:out.relative_to(root)
    except ValueError as exc:raise KnowledgeFoundationError('New snapshot must be inside root') from exc
    old_path=(root/base['source']['snapshot_path']).resolve()
    if out/'transcription.json'==old_path or out/'foundation.json'==foundation_path.resolve():
        raise KnowledgeFoundationError('New transcription cannot overwrite its base')
    original=load_json(old_path);snapshot=deepcopy(original);units=snapshot['units']
    touched=set();added=[]
    for operation in patch['operations']:
        unit=deepcopy(operation['unit']);locator=unit['locator']
        if locator in touched:raise KnowledgeFoundationError('A correction may touch a locator only once')
        touched.add(locator)
        locators=[u['locator'] for u in units]
        if operation['op']=='replace':
            if locator not in locators:raise KnowledgeFoundationError('Replacement locator is absent')
            units[locators.index(locator)]=unit
        else:
            if locator in locators or operation['after_locator'] not in locators:
                raise KnowledgeFoundationError('Insertion needs a new locator and existing explicit predecessor')
            units.insert(locators.index(operation['after_locator'])+1,unit);added.append(locator)
    unit_order={u['locator']:i for i,u in enumerate(units)}
    for unit in units:
        previous=unit.get('continues_from')
        if previous is not None and previous not in {u['locator'] for u in units}:
            raise KnowledgeFoundationError('Transcription continuation points to an absent unit')
        if previous is not None and unit_order[previous]>=unit_order[unit['locator']]:
            raise KnowledgeFoundationError('Continuation must reference an earlier source unit')
        box=unit.get('bbox')
        if box is not None and (not (box[0]<box[2] and box[1]<box[3])):
            raise KnowledgeFoundationError('Transcription bbox must have positive area')
    snapshot['revision']={'parent_snapshot_sha256':base['source']['snapshot_sha256'],
        'parent_snapshot_path':base['source']['snapshot_path'],'corrections_sha256':digest(patch),
        'reviewer':patch['reviewer'],'reviewer_type':patch['reviewer_type'],'model':patch['model'],
        'check_method':patch['check_method'],'checked_at':patch['checked_at'],
        'scope':'Only the declared changed/inserted transcription units were checked in this event.'}
    later=deepcopy(base)
    later['revision']={'id':'transcription-'+digest(patch)[:16],'parent_id':base['revision']['id'],
                      'note':'New source transcription; prior semantic annotations remain in the base artifact and require fresh modeling/review.'}
    later['source']['snapshot_path']=str((out/'transcription.json').relative_to(root))
    later['source']['snapshot_sha256']=digest_bytes(_bytes(snapshot))
    later['source']['transcription_method']=base['source']['transcription_method']+'; declarative original-source corrections/v1'
    old_units={u['locator']:u for u in base['units']};later['units']=[]
    for order,unit in enumerate(units):
        structural=unit['kind'] in {'blank','page_empty','page_number','header','paragraph_title','doc_title'}
        prior=old_units.get(unit['locator'],{})
        later['units'].append({'id':prior.get('id','u.'+digest_text(unit['locator'])[:16]),'order':order,
            'kind':unit['kind'],'locator':unit['locator'],'content_sha256':digest_text(unit['text']) if unit['text'] else None,
            'transcription_status':'verified' if unit['locator'] in touched else prior['transcription_status'],
            'interpretation_status':'not_applicable' if structural else 'unreviewed',
            'review_note':('Declared transcription correction checked by '+patch['reviewer']+'; semantic meaning needs independent review.') if unit['locator'] in touched else prior['review_note']})
    later['annotations']=[];later['relations']=[]
    later['scope']['included_locators']=[u['locator'] for u in units]
    later['scope']['context_locators']=[]
    validate(later)
    values={'corrections.json':patch,'transcription.json':snapshot,'foundation.json':later}
    manifest={'schema_version':'knowledge-transcription-revision/v1','base_foundation_sha256':digest_bytes(foundation_path.read_bytes()),
        'base_snapshot_sha256':base['source']['snapshot_sha256'],'source_sha256':base['source']['sha256'],
        'changed_locators':sorted(touched),'added_locators':added,'new_semantic_reviews':0,
        'outputs':{name:digest_bytes(_bytes(v)) for name,v in values.items()}}
    values['revision-manifest.json']=manifest
    # Check every preimage before any write, then allow identical-file recovery.
    for name,value in values.items():
        dest=out/name
        if dest.exists() and dest.read_bytes()!=_bytes(value):raise KnowledgeFoundationError('Immutable correction artifact differs: '+str(dest))
    out.mkdir(parents=True,exist_ok=True)
    for name,value in values.items():
        if not (out/name).exists():
            with (out/name).open('xb') as handle:handle.write(_bytes(value))
    validate(later,root=root,resolve=True)
    return {'artifact':str(out/'foundation.json'),'snapshot':str(out/'transcription.json'),
            'manifest':str(out/'revision-manifest.json'),'changed_locators':sorted(touched),'added_locators':added,'new_semantic_reviews':0}
