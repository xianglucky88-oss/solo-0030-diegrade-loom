from .archives import load_release, verify
from .errors import DomainError
from .timeline import replay_timeline


def sources(release):
    """按物理格点取当前有效测量及其设备原坐标，用于版差溯源。"""
    body = release['payload']
    timeline = replay_timeline(body['wafer'], body['events'])
    return {tuple(site): {'batch': entry['batch'],
                          'x': entry['record']['x'], 'y': entry['record']['y'],
                          'kind': entry['kind']}
            for site, entry in timeline.active.items()}


def compare(conn, wafer_id, first, second):
    if first >= second:
        raise DomainError('compare_order', '旧版号需小于新版号')
    before = load_release(conn, wafer_id, first)
    after = load_release(conn, wafer_id, second)
    verify(before)
    verify(after)
    old = {(r['x'], r['y']): r for r in before['payload']['rows']}
    old_sources = sources(before)
    new_sources = sources(after)
    changes = []
    for row in after['payload']['rows']:
        site = (row['x'], row['y'])
        previous = old[site]
        was, now = old_sources.get(site, {}), new_sources.get(site, {})
        moved = (was.get('x'), was.get('y')) != (now.get('x'), now.get('y'))
        if row == previous and not moved:
            continue
        if previous['bin'] != row['bin']:
            kind = 'grade'
        elif previous['batch'] != row['batch'] or moved:
            kind = 'evidence'
        else:
            continue
        changes.append({'site': list(site), 'before': previous['bin'], 'after': row['bin'], 'kind': kind,
                        'from_batch': previous['batch'], 'to_batch': row['batch'],
                        'from_device': [was.get('x'), was.get('y')], 'to_device': [now.get('x'), now.get('y')]})
    return {'from': first, 'to': second, 'changes': changes}
