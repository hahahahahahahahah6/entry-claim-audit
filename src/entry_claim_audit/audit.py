"""Offline entry-point auditing; project modules are parsed, never imported."""
import ast
import re
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.9–3.10
    tomllib = None


def fallback_metadata(text):
    """Read only simple quoted key/value pairs in the two script tables."""
    project = {}
    table = None
    for line in text.splitlines():
        header = re.fullmatch(r'\s*\[([^]]+)\]\s*(?:#.*)?', line)
        if header:
            name = header.group(1)
            table = project.setdefault(name.split('.')[1], {}) if name in ('project.scripts', 'project.gui-scripts') else None
        elif table is not None:
            pair = re.fullmatch(r'''\s*(?:"([^"\n]+)"|'([^'\n]+)'|([\w-]+))\s*=\s*(?:"([^"\n]*)"|'([^'\n]*)')\s*(?:#.*)?''', line)
            if pair:
                table[next(v for v in pair.groups()[:3] if v is not None)] = next(v for v in pair.groups()[3:] if v is not None)
    # Name is needed solely for documented-command matching.
    section = re.search(r'(?ms)^\s*\[project\]\s*\n(.*?)(?=^\s*\[|\Z)', text)
    if section:
        name = re.search(r'''(?m)^\s*name\s*=\s*["']([^"']+)["']''', section.group(1))
        if name:
            project['name'] = name.group(1)
    return {'project': project}


def resolve_module(root, module):
    parts = module.split('.')
    if any(p in {'tests', '.venv', '__pycache__', 'dist', '.git', '.egg-info'} or p.endswith('.egg-info') for p in parts):
        return None
    relative = Path(*parts)
    for base in (root / 'src', root):
        for candidate in (base / relative.with_suffix('.py'), base / relative / '__init__.py'):
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                return candidate
    return None


def symbols(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))} if isinstance(node, ast.ClassDef) else None
            result[node.name] = (True, methods)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                result[alias.asname or alias.name.split('.')[0]] = (True, None)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result[target.id] = (isinstance(node.value, (ast.Lambda, ast.Call)), None)
    return result


def code_lines(text):
    """Yield fenced and inline code with their original document line numbers."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            yield number, line.strip()
        else:
            for match in re.finditer(r'(`+)(.+?)\1', line):
                yield number, match.group(2).strip()


def audit(path='.', fail_on='MEDIUM', ignore=()):
    root = Path(path)
    if not root.is_dir():
        raise ValueError('project path must be a directory')
    metadata = root / 'pyproject.toml'
    data = {}
    if metadata.is_file():
        raw = metadata.read_text(encoding='utf-8')
        data = tomllib.loads(raw) if tomllib else fallback_metadata(raw)
    project = data.get('project', {})
    if not isinstance(project, dict):
        raise ValueError('project must be a table')
    if not any(table in project for table in ('scripts', 'gui-scripts')):
        return dict(status='PASS', findings=[], verified_scripts=[])
    findings, verified = [], []
    ignored = set(ignore)
    def add(rule, severity, file, line, message):
        if rule not in ignored:
            findings.append(dict(rule=rule, severity=severity, file=file, line=line, message=message))
    scripts = []
    for table in ('scripts', 'gui-scripts'):
        entries = project.get(table, {})
        if not isinstance(entries, dict):
            raise ValueError('entry-point declarations must be tables')
        scripts.extend((name, table, target) for name, target in entries.items())
    declared = {name for name, _, _ in scripts}
    for name in sorted(set(project.get('scripts', {})) & set(project.get('gui-scripts', {}))):
        add('script-name-duplicate', 'MEDIUM', 'pyproject.toml', 0, f'{name}: declared in scripts and gui-scripts')
    for name, table, raw in scripts:
        before = len(findings)
        prefix = f'{name} ({table})'
        target = raw.strip() if isinstance(raw, str) else ''
        pattern = r'[^\W\d]\w*(?:\.[^\W\d]\w*)*:[^\W\d]\w*(?:\.[^\W\d]\w*)*'
        if not re.fullmatch(pattern, target):
            add('script-target-malformed', 'HIGH', 'pyproject.toml', 0, f'{prefix}: malformed target {raw!r}')
            continue
        module, obj = target.split(':')
        source = resolve_module(root, module)
        if source is None:
            add('script-target-module-missing', 'HIGH', 'pyproject.toml', 0, f'{prefix}: module {module} missing')
            continue
        names = symbols(source)
        segments = obj.split('.')
        symbol = segments[0]
        if symbol not in names:
            add('script-target-symbol-missing', 'HIGH', 'pyproject.toml', 0, f'{prefix}: {module} symbol {symbol} missing')
            continue
        callable_, methods = names[symbol]
        if methods is not None and len(segments) > 1 and segments[1] not in methods:
            add('script-target-attr-missing', 'HIGH', 'pyproject.toml', 0, f'{prefix}: {module}:{symbol} method {segments[1]} missing')
        if not callable_:
            add('script-target-not-callable', 'MEDIUM', 'pyproject.toml', 0, f'{prefix}: {module}:{symbol} is a data assignment')
        if len(findings) == before:
            verified.append(dict(script=name, table=table, target=raw))
    docs = set(root.glob('*.md'))
    docs.update((root / 'docs').rglob('*.md'))
    texts = [(p, p.read_text(encoding='utf-8')) for p in sorted(docs) if p.is_file() and p.resolve().is_relative_to(root.resolve())]
    normalize = lambda value: value.lower().replace('_', '-')
    project_name = project.get('name')
    for doc, text in texts:
        if project_name:
            for line, command in code_lines(text):
                tokens = command.split()
                if tokens and normalize(tokens[0]) == normalize(project_name) and tokens[0] not in declared:
                    add('documented-command-missing', 'HIGH', str(doc.relative_to(root)), line, f'{tokens[0]} is not declared; declared scripts: {", ".join(sorted(declared)) or "(none)"}')
    has_docs = (root / 'README.md').is_file() or any(p.is_relative_to(root / 'docs') for p, _ in texts)
    if has_docs:
        for name in sorted(declared):
            if not any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text) for _, text in texts):
                add('script-undocumented', 'MEDIUM', 'pyproject.toml', 0, f'{name}: never mentioned in scanned docs')
    findings.sort(key=lambda f: (f['rule'], f['file'], f['line'], f['message']))
    failed = any(f['severity'] == 'HIGH' or fail_on == 'MEDIUM' for f in findings)
    return dict(status='FAIL' if failed else 'PASS', findings=findings, verified_scripts=sorted(verified, key=lambda s: (s['script'], s['table'], s['target'])))
