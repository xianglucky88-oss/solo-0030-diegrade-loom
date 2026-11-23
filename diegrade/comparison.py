from .archives import load_release, verify
from .errors import DomainError


def compare(conn, wafer_id, first, second):
    if first >= second:
        raise DomainError('compare_order', '旧版号需小于新版号')
    before = load_release(conn, wafer_id, first)
    after = load_release(conn, wafer_id, second)
    verify(before)
    verify(after)
    old = {(r['x'], r['y']): r for r in before['payload']['rows']}
    changes = []
    for row in after['payload']['rows']:
        previous = old[(row['x'], row['y'])]
        if row != previous:
            changes.append({'site': [row['x'], row['y']], 'before': previous['bin'], 'after': row['bin'],
                            'from_batch': previous['batch'], 'to_batch': row['batch'],
                            'kind': 'grade' if previous['bin'] != row['bin'] else 'evidence'})
    return {'from': first, 'to': second, 'changes': changes}
