"""Read-only, code-backed impact catalogue. Never imports business executors."""
from __future__ import annotations

import ast
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from app.services import business_relationship_catalog as catalog

APP_ROOT = Path(__file__).resolve().parents[1]
LOCK = APP_ROOT / 'assets' / 'business_relationship_sources.json'
STOP_NOTE = '保护关系只表示需要检查，禁止据此自动修改下游。全部视图均非实时业务完成证明。'


def _path(root, ref):
    name = ref['path']
    if not name.startswith('backend/app/'):
        raise ValueError('source outside app')
    path = (root / name.removeprefix('backend/app/')).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('source outside app')
    return path


def referenced_sources():
    return sorted({s['path'] for n in catalog.NODES + catalog.EDGES for s in n['sources']}
                  | {'backend/app/services/business_relationship_catalog.py'})


def fingerprints(root=APP_ROOT):
    out = {}
    for name in referenced_sources():
        path = _path(root, {'path': name})
        # Normalize checkout line endings, not substantive source changes.
        out[name] = sha256(path.read_text('utf-8-sig').replace('\r\n','\n').encode()).hexdigest() if path.is_file() else None
    return out


def validate():
    errors = []
    ids = [n['id'] for n in catalog.NODES]
    edge_ids = [e['id'] for e in catalog.EDGES]
    if len(ids) != len(set(ids)): errors.append('duplicate_node')
    if len(edge_ids) != len(set(edge_ids)): errors.append('duplicate_edge')
    domains = {d[0] for d in catalog.DOMAINS}
    for n in catalog.NODES:
        if n['domain'] not in domains: errors.append('unknown_domain:' + n['id'])
    for e in catalog.EDGES:
        if e['from'] not in ids or e['to'] not in ids: errors.append('dangling:' + e['id'])
        if not e['condition'] or not e['check'] or not e['sources']: errors.append('incomplete:' + e['id'])
    for f in catalog.FLOWS:
        if any(s not in ids for s in f['steps']): errors.append('invalid_flow:' + f['id'])
    return errors


def inventory(root=APP_ROOT):
    """Enumerate schema and unmapped files; no claim that a file ref covers all its fields."""
    referenced = set(referenced_sources())
    models, files, errors = [], [], []
    for folder in ('models', 'services', 'api'):
        for path in sorted((root / folder).glob('*.py')):
            if path.name == '__init__.py': continue
            relative = 'backend/app/' + path.relative_to(root).as_posix()
            if path.name.startswith('business_relationship'): continue
            files.append({'path':relative, 'referenced':relative in referenced})
            if folder != 'models': continue
            try:
                tree = ast.parse(path.read_text('utf-8-sig'))
                for cls in tree.body:
                    if not isinstance(cls, ast.ClassDef): continue
                    table = next((ast.literal_eval(x.value) for x in cls.body if isinstance(x,ast.Assign)
                                  and any(isinstance(t,ast.Name) and t.id=='__tablename__' for t in x.targets)), None)
                    if table:
                        models.append({'model':cls.name,'table':table,'path':relative,
                                       'fields':[x.target.id for x in cls.body if isinstance(x,ast.AnnAssign) and isinstance(x.target,ast.Name)],
                                       'detail_status':'字段逐项语义仍需按关系明细核对'})
            except (SyntaxError, ValueError, OSError) as exc:
                errors.append({'path':relative,'type':type(exc).__name__})
    return {'models':models, 'files':files, 'errors':errors,
            'model_count':len(models), 'field_count':sum(len(m['fields']) for m in models),
            'file_count':len(files), 'referenced_file_count':sum(f['referenced'] for f in files),
            'unmapped_files':[f['path'] for f in files if not f['referenced']],
            'note':'文件被引用不代表全部字段、分支、运行与外部同步已验证；未引用文件不能判定无影响。'}


def snapshot(root=APP_ROOT, lock=LOCK):
    try:
        baseline = json.loads(lock.read_text('utf-8'))
    except (OSError, ValueError):
        baseline = {}
    current = fingerprints(root)
    states = {}
    for path, digest in current.items():
        states[path] = ('missing' if digest is None else 'unreviewed' if path not in baseline.get('sources', {})
                        else 'changed' if baseline['sources'][path] != digest else 'matching')
    def with_sources(item):
        result = {**item, 'sources':[]}
        for ref in item['sources']:
            path = _path(root,ref)
            lines = path.read_text('utf-8-sig').splitlines() if path.is_file() else []
            line = next((i+1 for i,s in enumerate(lines) if ref['anchor'] and ref['anchor'] in s),None)
            state = states[ref['path']]
            if ref['anchor'] and line is None: state = 'anchor_missing'
            result['sources'].append({**ref,'line':line,'state':state})
        result['source_current'] = bool(result['sources']) and all(s['state']=='matching' for s in result['sources'])
        return result
    return {'version':catalog.VERSION, 'reviewed_at':baseline.get('reviewed_at'),
            'read_only':True, 'runtime_verified':False, 'notice':STOP_NOTE,
            'domains':[{'id':i,'label':l,'owner':o} for i,l,o in catalog.DOMAINS],
            'nodes':[with_sources(n) for n in catalog.NODES],
            'edges':[with_sources(e) for e in catalog.EDGES], 'flows':catalog.FLOWS,
            'source_states':states,'validation_errors':validate(), 'coverage':inventory(root)}


def impact(node_ids, *, direction='downstream', depth=6, data=None):
    """Bounded reachability for review, including protection and uncertain edges.

    A path is a checklist, never automatic write permission. All qualifying edges
    are returned, including alternate paths and cycles; nodes are visited once.
    """
    data = data or snapshot()
    if direction not in ('downstream','upstream'): raise ValueError('invalid_direction')
    if not 1 <= depth <= 12: raise ValueError('invalid_depth')
    known = {n['id'] for n in data['nodes']}
    if not node_ids or set(node_ids)-known: raise ValueError('unknown_node')
    distances = dict.fromkeys(node_ids, 0)
    queue = deque(node_ids)
    selected = {}
    a,b = ('from','to') if direction=='downstream' else ('to','from')
    while queue:
        current = queue.popleft()
        if distances[current] >= depth: continue
        for e in data['edges']:
            if e[a] != current: continue
            selected[e['id']] = e
            if e[b] not in distances:
                distances[e[b]] = distances[current]+1
                queue.append(e[b])
    truncated = any(distances.get(e[a])==depth and e[b] not in distances for e in data['edges'])
    return {'roots':node_ids,'direction':direction,'depth':depth,'truncated':truncated,
            'nodes':[{**n,'distance':distances[n['id']]} for n in data['nodes'] if n['id'] in distances],
            'edges':list(selected.values()), 'notice':STOP_NOTE}


def change_report(paths, *, data=None):
    data=data or snapshot()
    paths={p.replace('\\','/').removeprefix('./') for p in paths}
    hit=set()
    referenced=set()
    for n in data['nodes']:
        refs={s['path'] for s in n['sources']}; referenced |= refs
        if paths & refs: hit.add(n['id'])
    for e in data['edges']:
        refs={s['path'] for s in e['sources']}; referenced |= refs
        if paths & refs: hit.update([e['from'],e['to']])
    return {'changed_paths':sorted(paths),'unmapped_changes':sorted(paths-referenced),
            'impact':impact(sorted(hit),data=data) if hit else None,
            'decision':'需要逐项审查，不自动修改业务；未覆盖文件不是无影响',
            'validation_errors':data['validation_errors']}


def field_references(model, field, *, root=APP_ROOT):
    """Conservative source candidates, NOT a type-resolved data lineage proof."""
    record=next((m for m in inventory(root)['models'] if m['model']==model), None)
    if not record or field not in record['fields']:
        raise ValueError('unknown_model_or_field')
    hits, errors=[],[]
    for path in sorted(root.rglob('*.py')):
        if '__pycache__' in path.parts: continue
        relative='backend/app/'+path.relative_to(root).as_posix()
        try:
            tree=ast.parse(path.read_text('utf-8-sig'))
        except (SyntaxError,OSError) as exc:
            errors.append({'path':relative,'type':type(exc).__name__});continue
        seen=set()
        for item in ast.walk(tree):
            kind=None
            if isinstance(item,ast.Attribute) and item.attr==field:
                kind='属性赋值候选' if isinstance(item.ctx,ast.Store) else '属性读取候选'
            elif isinstance(item,ast.Call):
                if isinstance(item.func,ast.Name) and item.func.id in ('getattr','setattr','hasattr') and len(item.args)>1 and isinstance(item.args[1],ast.Constant) and item.args[1].value==field:
                    kind='动态属性候选'
                elif any(k.arg==field for k in item.keywords):
                    kind='同名参数候选'
            if kind and (item.lineno,kind) not in seen:
                seen.add((item.lineno,kind))
                hits.append({'path':relative,'line':item.lineno,'kind':kind,
                             'receiver':ast.unparse(item.value)[:100] if isinstance(item,ast.Attribute) else ''})
    return {'model':model,'field':field,'declaration':record['path'],
            'candidates':hits[:500],'total':len(hits),'truncated':len(hits)>500,'errors':errors,
            'notice':'同名字段可能属于其他对象，动态SQL/JSON/反射也可能漏检；这是代码候选引用，不是已验证业务关系。',
            'review':change_report(sorted({h['path'] for h in hits}))}
