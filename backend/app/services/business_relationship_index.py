"""Static names and import links only; never execute scanned application modules."""
from __future__ import annotations

import ast
import json
from hashlib import sha256
from pathlib import Path
import re

NOTICE = '静态声明与导入是代码检查线索，不是已审核业务关系；动态调用、JSON、SQL和反射仍需人工核查。'


def source_index(app_root: Path, *, use_packaged=True):
    repo = app_root.parent.parent
    scopes = [('backend/app', app_root, {'.py'}),
              ('scripts', repo / 'scripts', {'.py'}),
              ('frontend/src', repo / 'frontend/src', {'.ts', '.tsx'})]
    files, errors, missing = [], [], []
    for prefix, folder, extensions in scopes:
        if not folder.is_dir():
            missing.append(prefix)
            continue
        for path in sorted(folder.rglob('*')):
            if path.suffix not in extensions or not path.is_file():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                continue
            if any(p in ('__pycache__', 'node_modules', 'tests', 'test', '.git') for p in path.relative_to(folder).parts):
                continue
            if path.name.startswith(('test_', 'business_relationship')):
                continue
            relative = prefix + '/' + path.relative_to(folder).as_posix()
            row = {'path': relative, 'declarations': [], 'imports': [], 'status': 'indexed', 'origin': 'live_source',
                   'semantic_reviewed': False}
            files.append(row)
            try:
                text = path.read_text('utf-8-sig').replace('\r\n', '\n')
                row['sha256'] = sha256(text.encode()).hexdigest()
                if path.suffix == '.py':
                    tree = ast.parse(text)
                    for item in ast.walk(tree):
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            row['declarations'].append({'name': item.name, 'line': item.lineno,
                                                        'kind': 'class' if isinstance(item, ast.ClassDef) else 'function'})
                        elif isinstance(item, ast.Import):
                            row['imports'].extend({'name': a.name, 'line': item.lineno, 'level': 0} for a in item.names)
                        elif isinstance(item, ast.ImportFrom):
                            row['imports'].append({'name': item.module or '', 'line': item.lineno, 'level': item.level})
                            row['imports'].extend({'name': '.'.join(filter(None, (item.module, a.name))),
                                                   'line': item.lineno, 'level': item.level} for a in item.names if a.name != '*')
                else:
                    # Explicitly candidate-only; not a TypeScript parser or data-flow proof.
                    for lineno, line in enumerate(text.splitlines(), 1):
                        for match in re.finditer(r'\b(?:function|class|interface|type|const)\s+([A-Za-z_$][\w$]*)', line):
                            row['declarations'].append({'name': match.group(1), 'line': lineno, 'kind': 'typescript_candidate'})
                        match = re.search(r'\bfrom\s+[\'"]([^\'"]+)[\'"]', line)
                        if match:
                            row['imports'].append({'name': match.group(1), 'line': lineno, 'level': 0})
            except (SyntaxError, UnicodeError, OSError) as exc:
                row['status'] = 'parse_error'
                errors.append({'path': relative, 'type': type(exc).__name__})
    packaged_dependencies = []
    packaged_scopes = []
    packaged_file = app_root / 'assets' / 'business_relationship_index.json'
    if missing and use_packaged and packaged_file.is_file():
        try:
            package = json.loads(packaged_file.read_text('utf-8'))
            if package.get('schema') != 'relationship-static-index-v1': raise ValueError('schema')
            if not isinstance(package.get('files'), list) or not isinstance(package.get('dependencies'), list): raise ValueError('shape')
            for f in package['files']:
                if (not isinstance(f.get('path'), str) or '..' in f['path'].split('/') or ':' in f['path']
                    or f['path'].startswith('/') or not isinstance(f.get('declarations'), list)):
                    raise ValueError('entry')
            for e in package['dependencies']:
                if not all(k in e for k in ('from', 'to', 'line')): raise ValueError('dependency')
            for prefix in list(missing):
                entries = [f for f in package['files'] if f['path'].startswith(prefix + '/')]
                if not entries: continue
                files.extend({**f, 'imports': [], 'origin': 'published_source_snapshot', 'semantic_reviewed': False} for f in entries)
                packaged_scopes.append(prefix)
                missing.remove(prefix)
            packaged_dependencies = package['dependencies']
            errors.extend(package.get('errors', []))
        except (OSError, ValueError, KeyError, TypeError):
            errors.append({'path': 'packaged-source-index', 'type': 'InvalidIndex'})
    known = {f['path'] for f in files}
    dependencies = []
    for row in files:
        seen = set()
        for imp in row.pop('imports'):
            name = imp['name']
            if row['path'].endswith('.py'):
                if imp['level']:
                    base = Path(row['path']).parent
                    for _ in range(imp['level'] - 1): base = base.parent
                    candidate = (base / name.replace('.', '/')).as_posix() + '.py'
                elif name.startswith('app.'):
                    candidate = 'backend/' + name.replace('.', '/') + '.py'
                elif name.startswith('scripts.'):
                    candidate = name.replace('.', '/') + '.py'
                else:
                    candidate = 'scripts/' + name.replace('.', '/') + '.py'
                candidates = [candidate]
            else:
                if not name.startswith('.'): continue
                # Resolve against a synthetic root; only exact known paths are kept.
                parts = list(Path(row['path']).parent.parts)
                for part in name.split('/'):
                    if part == '..':
                        if parts: parts.pop()
                    elif part not in ('.', ''): parts.append(part)
                base = '/'.join(parts)
                candidates = [base + suffix for suffix in ('.ts', '.tsx', '/index.ts', '/index.tsx')]
            for target in candidates:
                if target in known and target != row['path'] and (target, imp['line']) not in seen:
                    seen.add((target, imp['line']))
                    dependencies.append({'from': row['path'], 'to': target, 'line': imp['line'],
                                         'evidence': 'static_import_candidate'})
    # Backend facts stay live; only absent published scopes use build-time metadata.
    dependencies.extend(e for e in packaged_dependencies if e['from'] in known and e['to'] in known
                        and any(e['from'].startswith(p + '/') for p in packaged_scopes))
    return {'schema': 'relationship-static-index-v1', 'files': files, 'dependencies': dependencies, 'errors': errors,
            'missing_scopes': missing, 'packaged_scopes': packaged_scopes, 'notice': NOTICE, 'runtime_verified': False}


def dependency_review(paths, index, depth=3):
    """Reverse-import reachability for change review; unknown names never become safe."""
    if not 1 <= depth <= 12: raise ValueError('invalid_depth')
    if not paths or len(paths) > 100: raise ValueError('invalid_paths')
    paths = sorted(set(p.replace('\\', '/').removeprefix('./').strip() for p in paths))
    if any(not p or len(p) > 300 or '..' in p.split('/') or ':' in p or p.startswith('/') for p in paths):
        raise ValueError('invalid_paths')
    known = {f['path'] for f in index['files']}
    distance = {p: 0 for p in paths if p in known}
    queue = list(distance)
    edges = []
    for current in queue:
        if distance[current] >= depth: continue
        for e in index['dependencies']:
            if e['to'] != current: continue
            edges.append(e)
            if e['from'] not in distance:
                distance[e['from']] = distance[current] + 1
                queue.append(e['from'])
    truncated = any(distance.get(e['to']) == depth and e['from'] not in distance for e in index['dependencies'])
    return {'changed_paths': paths, 'unknown_paths': sorted(set(paths)-known),
            'files': [{**f, 'distance': distance[f['path']]} for f in index['files'] if f['path'] in distance],
            'dependencies': edges, 'truncated': truncated, 'notice': NOTICE}


if __name__ == '__main__':
    import sys
    result = source_index(Path(sys.argv[1]), use_packaged=False)
    if result['missing_scopes'] or result['errors']:
        raise SystemExit('静态索引有缺失或解析异常，停止生成发布索引')
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
