from . import events
from .codec import decode, digest, encode
from .errors import DomainError
from .sorter import assignments
from .timeline import replay_timeline
from .validation import fields
from .wafers import load_wafer

FORMAT = 'diegrade.release.1'


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
    rows = assignments(replay_timeline(spec, evidence))
    body = {'format': FORMAT, 'revision': revision, 'wafer': spec, 'events': evidence, 'rows': rows}
    release = {'payload': body, 'sha256': digest(body)}
    conn.execute('INSERT INTO releases VALUES (?, ?, ?)', (wafer_id, revision, encode(release)))
    return release


def verify(release):
    body = release['payload']
    if body['format'] != FORMAT or digest(body) != release['sha256']:
        raise DomainError('release_digest', '分选版结构或摘要不一致', 409)
    computed = assignments(replay_timeline(body['wafer'], body['events']))
    if computed != body['rows']:
        raise DomainError('release_replay', '现场复算与已归档分选计划不一致', 409)
    return {'rows': len(computed), 'sha256': release['sha256'], 'revision': body['revision'], 'verified': True}
