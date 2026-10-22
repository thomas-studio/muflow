"""Independently compare source episode headings, registration and request coverage.

This is a local inspection helper. Novel chapters are deliberately not episode
boundaries, and project registration alone is never an independent baseline.
"""
from pathlib import Path
import re
from core import load, resolve, sha

_NUMBERS = {'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}
_HEADING = re.compile(r'^[ \t]*(?:#{1,6}[ \t]*)?第[ \t]*([0-9零〇一二两三四五六七八九十百千万]+)[ \t]*集', re.M)


def episode_number(value):
    text = str(value).strip()
    if text.isdecimal():
        return int(text)
    if not text or any(c not in _NUMBERS and c not in '十百千万' for c in text):
        raise ValueError('Invalid episode number: '+text)
    if not any(c in text for c in '十百千万'):
        return int(''.join(str(_NUMBERS[c]) for c in text))
    total = section = number = 0
    for c in text:
        if c in _NUMBERS:
            number = _NUMBERS[c]
        elif c == '万':
            total += (section+number)*10000
            section = number = 0
        else:
            section += (number or 1)*{'十':10,'百':100,'千':1000}[c]
            number = 0
    return total+section+number


def _ids(values):
    if not isinstance(values, (list, tuple, set)):
        return []
    out = set()
    for value in values:
        try:
            number = episode_number(value.get('id', value.get('episode')) if isinstance(value, dict) else value)
            if number > 0:
                out.add(number)
        except (TypeError, ValueError):
            pass
    return sorted(out)


def _text(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _text(item)]
    if isinstance(value, dict):
        # Read original extracted paragraphs only, never project episode records.
        if isinstance(value.get('paragraphs'), list):
            return _text(value['paragraphs'])
        for key in ('text', 'content'):
            if isinstance(value.get(key), str):
                return [value[key]]
    return []


def _read_source(path):
    if path.suffix.lower() == '.json':
        return '\n'.join(_text(load(path)))
    raw = path.read_bytes()
    for encoding in ('utf-8-sig', 'utf-16', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise ValueError('Source encoding could not be read')


def coverage(project, base):
    """Return full-source and current-scope coverage without changing the project.

    A confirmed explicit baseline must bind to the current source SHA256 and
    contain evidence. Source headings are also independent evidence. Unknown
    baselines remain visible and do not stop early local preview creation.
    """
    registered = _ids(project.get('episodes', []))
    grouped_values = []
    for group in project.get('groups',[]):
        actual_ids = {shot.get('id') for shot in group.get('shots',[]) if shot.get('id')}
        if not actual_ids:
            continue
        if group.get('uses'):
            grouped_values.extend(use.get('episode') for use in group['uses']
                                  if actual_ids & set(use.get('shot_ids',[])))
        else:
            grouped_values.append(group.get('episode'))
    grouped = _ids(grouped_values)
    issues = []
    source_ids = []
    source_path = project.get('source_path')
    source_hash = None
    source_kind = 'unverified'
    evidence = []
    basis_status = 'unverified'
    path = resolve(base, source_path) if source_path else None
    if path:
        try:
            source_hash = sha(path)
            text = _read_source(path)
            matches = list(_HEADING.finditer(text))
            source_ids = _ids([m.group(1) for m in matches])
            if source_ids:
                source_kind = 'source_episode_headings'
                basis_status = 'verified'
                evidence = [{'heading':m.group(0).strip(), 'line':text.count('\n', 0, m.start())+1} for m in matches]
        except (OSError, ValueError, TypeError) as exc:
            issues.append({'id':'SOURCE_READ', 'actual':str(exc)})

    baseline = project.get('episode_baseline') or (project.get('source_coverage') or {}).get('episode_baseline')
    claimed = isinstance(baseline,dict) and any(baseline.get(k) for k in ('confirmed','episode_ids','episodes','source_sha256','evidence'))
    if claimed:
        declared = _ids(baseline.get('episode_ids', baseline.get('episodes', [])))
        bound = bool(source_hash and baseline.get('source_sha256') == source_hash)
        if baseline.get('confirmed') is True and declared and baseline.get('evidence') and bound:
            if source_ids and source_ids != declared:
                issues.append({'id':'SOURCE_BASELINE_CONFLICT', 'actual':'确认分集基准与原文集标题不一致', 'source_episodes':source_ids, 'baseline_episodes':declared})
                basis_status = 'unverified'
                source_ids = sorted(set(source_ids)|set(declared))
                source_kind = 'conflicting_baselines'
            else:
                source_ids = declared
                source_kind = 'confirmed_episode_baseline'
                basis_status = 'verified'
                evidence = [baseline['evidence']]
        else:
            issues.append({'id':'SOURCE_BASELINE_UNVERIFIED', 'actual':'显式分集基准须确认、列出集号、附依据，并匹配当前原稿哈希'})
            # A stale explicit declaration must not silently inherit approval.
            basis_status = 'unverified'

    if not source_ids:
        issues.append({'id':'SOURCE_EPISODES_UNVERIFIED', 'actual':'未取得独立原剧本集标题或与原稿绑定的已确认分集基准；小说章节不当作集数'})
    scope = project.get('workflow', {}).get('production_scope') or project.get('production_scope') or {}
    if isinstance(scope, list):
        scope = {'episodes':scope}
    requested = _ids(scope.get('episodes', [])) if isinstance(scope, dict) else []
    scope_explicit = bool(requested and (scope.get('user_evidence') or scope.get('evidence')))
    if not requested:
        requested = list(source_ids if source_ids else registered)
    if scope and requested and not scope_explicit:
        issues.append({'id':'SCOPE_EVIDENCE', 'actual':'本次范围已登记但缺少用户明确范围依据；不能据此缩小全剧检查'})

    source_set, registered_set, grouped_set, requested_set = map(set, (source_ids, registered, grouped, requested))
    missing_registration = sorted(source_set-registered_set)
    missing_groups = sorted(source_set-grouped_set)
    missing_requested = sorted(requested_set-(registered_set & grouped_set))
    extra_registered = sorted(registered_set-source_set) if source_ids else []
    outside_source = sorted(requested_set-source_set) if source_ids else []
    full_complete = bool(source_ids and basis_status == 'verified' and not missing_registration and not missing_groups and not extra_registered)
    scope_complete = bool(requested and not missing_requested and not outside_source)
    legitimate_scope = not scope or scope_explicit or requested_set == source_set
    complete = bool(basis_status == 'verified' and scope_complete and legitimate_scope and not extra_registered)
    return {'basis_status':basis_status, 'source_kind':source_kind, 'source_path':str(source_path) if source_path else None,
            'source_sha256':source_hash, 'evidence':evidence, 'source_episodes':source_ids, 'source_count':len(source_ids),
            'registered_episodes':registered, 'grouped_episodes':grouped, 'requested_episodes':requested,
            'missing_registration':missing_registration, 'missing_groups':missing_groups, 'missing_requested':missing_requested,
            'extra_registered':extra_registered, 'outside_source':outside_source, 'scope_explicit':scope_explicit,
            'full_complete':full_complete, 'scope_complete':scope_complete, 'complete':complete, 'issues':issues,
            'original_range':source_ids, 'current_range':requested, 'planned':grouped,
            'missing':sorted(source_set-(registered_set & grouped_set))}
