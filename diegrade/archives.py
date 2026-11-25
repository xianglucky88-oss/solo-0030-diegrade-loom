from . import events
from .codec import decode, digest, encode
from .errors import DomainError
from .sorter import assignments
from .timeline import replay_timeline
from .validation import fields
from .wafers import load_wafer

FORMAT = 'diegrade.release.2'
LEGACY = 'diegrade.release.1'


def load_release(conn, wafer_id, revision):
    stored = conn.execute('SELECT body FROM releases WHERE wafer=? AND revision=?', (wafer_id, revision)).fetchone()
    if not stored:
        raise DomainError('release_missing', '分选版不存在', 404)
    return decode(stored[0])


def seal(conn, wafer_id, data):
    fields(data, 'expected')
    spec, revision = load_wafer(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    try:
        return load_release(conn, wafer_id, revision)
    except DomainError as exc:
        if exc.code != 'release_missing':
            raise
    evidence = events.read(conn, wafer_id)
    timeline = replay_timeline(spec, evidence)
    rows = assignments(timeline)
    body = {'format': FORMAT, 'revision': revision, 'wafer': spec, 'events': evidence,
            'rounds': timeline.round_views(), 'rows': rows}
    release = {'payload': body, 'sha256': digest(body)}
    conn.execute('INSERT INTO releases VALUES (?, ?, ?)', (wafer_id, revision, encode(release)))
    return release


ROUND_KINDS = frozenset(('round', 'submission', 'verdict', 'void'))


def verify(release):
    body = release['payload']
    if body.get('format') not in (FORMAT, LEGACY) or digest(body) != release['sha256']:
        raise DomainError('release_digest', '分选版结构或摘要不一致', 409)
    has_rounds = any(event.get('kind') in ROUND_KINDS for event in body['events'])
    if body['format'] == LEGACY and has_rounds:
        raise DomainError('release_digest', '含两站轮次的签件不能按旧格式复算', 409)
    timeline = replay_timeline(body['wafer'], body['events'])
    computed = assignments(timeline)
    if computed != body['rows']:
        raise DomainError('release_replay', '现场复算与已归档分选计划不一致', 409)
    if body['format'] == FORMAT:
        verify_rounds(timeline, body)
    return {'rows': len(computed), 'sha256': release['sha256'], 'revision': body['revision'], 'verified': True,
            'rounds': len(body.get('rounds', []))}


def verify_rounds(timeline, body):
    """随件轮次材料须与现场重放一致，并证明候选齐备、裁决与最终分选的关系。"""
    if body.get('rounds') != timeline.round_views():
        raise DomainError('release_rounds', '封存的复测轮次证据与现场记录不一致', 409)
    rows = {(row['x'], row['y']): row for row in body['rows']}
    decided = {}
    for view in body['rounds']:
        site = tuple(view['site'])
        stations = view['stations']
        candidates = {item['station']: item for item in view['submissions']}
        if len(stations) != 2 or len(set(stations)) != 2:
            raise DomainError('release_round', f'轮次 {view["id"]} 未登记两个不同工位', 409)
        if set(candidates) != set(stations):
            raise DomainError('release_round', f'轮次 {view["id"]} 缺少某一工位的来件', 409)
        if any(item['parent'] != view['parent'] or tuple(item['site']) != site for item in candidates.values()):
            raise DomainError('release_round', f'轮次 {view["id"]} 的来件并非同一点、同一父量测的竞争', 409)
        if view['status'] == 'void':
            if any(item['state'] != 'review' for item in candidates.values()) or view['verdict']:
                raise DomainError('release_round', f'轮次 {view["id"]} 并非两站开路后作废', 409)
            continue
        if view['status'] != 'decided':
            raise DomainError('release_round', f'轮次 {view["id"]} 未完成裁决便进入封存', 409)
        chosen = view['verdict']['station']
        if chosen not in candidates:
            raise DomainError('release_round', f'轮次 {view["id"]} 的裁决未指向本轮来件', 409)
        if candidates[chosen]['state'] not in ('a', 'b', 'reject'):
            raise DomainError('release_round', f'轮次 {view["id"]} 采用了不能终判的来件', 409)
        decided[view['id']] = view
    for site, active in timeline.active.items():
        if active.get('event') != 'verdict':
            continue
        view = decided.get(active['round'])
        if not view or tuple(view['site']) != site or view['verdict']['station'] != active['station']:
            raise DomainError('release_round', f'格点 {site} 的最终分选并非该轮裁决的来件', 409)
        picked = next(item for item in view['submissions'] if item['station'] == active['station'])
        row = rows.get(site)
        if not row or row['bin'] != picked['state'] or row['batch'] != picked['batch']:
            raise DomainError('release_round', f'轮次 {view["id"]} 的裁决与最终分选不一致', 409)
