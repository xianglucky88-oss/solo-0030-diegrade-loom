from . import events
from .codec import decode, encode
from .errors import DomainError
from .geometry import order
from .grading import FINAL
from .timeline import replay_timeline
from .validation import fields, identifier, integer, reason
from .wafers import load_wafer


def prepare(data):
    fields(data, 'id', 'card', 'tip', 'segment', 'reason')
    return {'id': identifier(data['id'], '停用编号'), 'card': identifier(data['card'], '探针卡号'),
            'tip': integer(data['tip'], 1, 99999, '触点号'), 'segment': identifier(data['segment'], '工作段标识'),
            'reason': reason(data['reason'], '停用依据')}


def catalog(conn):
    return [decode(body) for (body,) in conn.execute('SELECT body FROM suspensions ORDER BY id')]


def matching(conn):
    return {(item['card'], item['tip'], item['segment']) for item in catalog(conn)}


def affected_sites(timeline, key):
    sites = []
    for site in order(timeline.physical):
        if timeline.state(site) not in FINAL:
            continue
        record = timeline.active[site]['record']
        if (record['card'], record['tip'], record['segment']) == key:
            sites.append(site)
    return sites


def confirm(conn, data):
    note = prepare(data)
    stored = conn.execute('SELECT body FROM suspensions WHERE id=?', (note['id'],)).fetchone()
    if stored:
        record = decode(stored[0])
        if any(record[key] != note[key] for key in note):
            raise DomainError('suspend_conflict', '停用编号已用于另一条停用记录', 409)
        return {**record, 'duplicate': True}
    key = (note['card'], note['tip'], note['segment'])
    affected = []
    for (wafer_id,) in conn.execute('SELECT id FROM wafers ORDER BY id'):
        spec, revision = load_wafer(conn, wafer_id)
        timeline = replay_timeline(spec, events.read(conn, wafer_id))
        sites = affected_sites(timeline, key)
        if sites:
            body = {**note, 'sites': [list(site) for site in sites]}
            events.append(conn, wafer_id, revision + 1, 'suspend', note['id'], body)
            affected.append({'wafer': wafer_id, 'revision': revision + 1,
                             'sites': [list(site) for site in sites]})
    record = {**note, 'affected': affected}
    conn.execute('INSERT INTO suspensions VALUES (?, ?)', (note['id'], encode(record)))
    return {**record, 'duplicate': False}
