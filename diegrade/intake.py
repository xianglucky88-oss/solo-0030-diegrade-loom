from . import events
from .batches import prepare
from .errors import DomainError
from .geometry import point
from .timeline import replay_timeline
from .validation import fields, identifier, reason
from .wafers import load_wafer


def current(conn, wafer_id):
    spec, revision = load_wafer(conn, wafer_id)
    return replay_timeline(spec, events.read(conn, wafer_id)), revision


def intake(conn, wafer_id, data, write):
    fields(data, 'expected', 'batch')
    batch = prepare(data['batch'])
    spec, revision = load_wafer(conn, wafer_id)
    duplicate = events.find(conn, wafer_id, 'batch', batch['id'])
    events.check_revision(data['expected'], revision)
    if duplicate is not None:
        if duplicate != batch:
            raise DomainError('batch_conflict', '批次编号已用于另一份证据', 409)
        return {'revision': revision, 'duplicate': True, 'changes': []}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    changes = timeline.apply_batch(batch, revision + 1)
    if write:
        events.append(conn, wafer_id, revision + 1, 'batch', batch['id'], batch)
    return {'revision': revision + int(write), 'duplicate': False,
            'changes': [{'site': list(site), 'state': result['state']} for site, result in changes]}


def hold(conn, wafer_id, data):
    fields(data, 'expected', 'id', 'site', 'reason')
    site = point(data['site'])
    hold_id = identifier(data['id'])
    body = {'id': hold_id, 'site': list(site), 'reason': reason(data['reason'])}
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    old = events.find(conn, wafer_id, 'hold', hold_id)
    if old:
        if old != body:
            raise DomainError('hold_conflict', '复核编号已用于另一条现场记录', 409)
        return {'revision': revision, 'duplicate': True}
    timeline.cell(site)
    timeline.apply_hold(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'hold', hold_id, body)
    return {'revision': revision + 1, 'duplicate': False}
