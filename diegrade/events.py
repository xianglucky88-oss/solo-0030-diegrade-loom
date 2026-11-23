from .codec import decode, encode
from .errors import DomainError
from .validation import integer

LIMIT = 128


def read(conn, wafer_id):
    return [{'revision': revision, 'kind': kind, 'body': decode(body)} for revision, kind, body in conn.execute(
        'SELECT revision, kind, body FROM events WHERE wafer=? ORDER BY revision', (wafer_id,))]


def check_revision(expected, actual):
    integer(expected, 0, LIMIT, '现场代次')
    if expected != actual:
        raise DomainError('stale', f'现场已变为第 {actual} 代，请刷新后再提交', 409)


def find(conn, wafer_id, kind, ref):
    item = conn.execute('SELECT body FROM events WHERE wafer=? AND kind=? AND ref=?', (wafer_id, kind, ref)).fetchone()
    return decode(item[0]) if item else None


def append(conn, wafer_id, revision, kind, ref, body):
    if revision > LIMIT:
        raise DomainError('limit', f'本片现场记录已达 {LIMIT} 条上限', 409)
    conn.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?)', (wafer_id, revision, kind, ref, encode(body)))
    conn.execute('UPDATE wafers SET revision=? WHERE id=?', (revision, wafer_id))
