from . import events
from .batches import prepare
from .errors import DomainError
from .frames import frame
from .geometry import point
from .probe_csv import check_record
from .timeline import replay_timeline
from .validation import fields, identifier, integer, reason
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


def prepare_consignment(data):
    fields(data, 'id', 'frame', 'csv')
    if not isinstance(data['csv'], str):
        raise DomainError('csv_size', '来件须为 CSV 文本')
    return {'id': identifier(data['id']), 'frame': frame(data['frame']), 'csv': data['csv']}


def consignment(conn, wafer_id, data, write):
    fields(data, 'expected', 'consignment')
    body = prepare_consignment(data['consignment'])
    spec, revision = load_wafer(conn, wafer_id)
    duplicate = events.find(conn, wafer_id, 'consignment', body['id'])
    events.check_revision(data['expected'], revision)
    if duplicate is not None:
        if duplicate != body:
            raise DomainError('consignment_conflict', '来件编号已用于另一份证据', 409)
        return {'revision': revision, 'duplicate': True, 'accepted': [], 'quarantined': []}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    lines = timeline.apply_consignment(body, revision + 1)
    if write:
        events.append(conn, wafer_id, revision + 1, 'consignment', body['id'], body)
    return {'revision': revision + int(write), 'duplicate': False,
            'accepted': [{'line': number, 'site': item['site'], 'state': item['state']}
                         for number, item in lines.items() if item['status'] == 'accepted'],
            'quarantined': [{'line': number, 'site': item['site'], 'reason': item['reason']}
                            for number, item in lines.items() if item['status'] == 'quarantined']}


def prepare_rework(data):
    fields(data, 'id', 'source', 'lines')
    if not isinstance(data['lines'], list) or not 1 <= len(data['lines']) <= 4096:
        raise DomainError('lines', '行处置须为 1–4096 条记录')
    lines = []
    for item in data['lines']:
        if not isinstance(item, dict) or item.get('action') not in ('revoke', 'correct'):
            raise DomainError('action', '每行处置须为 revoke 或 correct')
        line = {'line': integer(item.get('line'), 2, 4097, '原始行号'), 'action': item['action']}
        if item['action'] == 'revoke':
            fields(item, 'line', 'action')
        else:
            fields(item, 'line', 'action', 'record')
            line['record'] = check_record(item['record'])
        lines.append(line)
    return {'id': identifier(data['id']), 'source': identifier(data['source']), 'lines': lines}


def rework(conn, wafer_id, data):
    fields(data, 'expected', 'rework')
    body = prepare_rework(data['rework'])
    spec, revision = load_wafer(conn, wafer_id)
    duplicate = events.find(conn, wafer_id, 'rework', body['id'])
    events.check_revision(data['expected'], revision)
    if duplicate is not None:
        if duplicate != body:
            raise DomainError('rework_conflict', '更正编号已用于另一份证据', 409)
        return {'revision': revision, 'duplicate': True, 'revoked': [], 'corrected': []}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    outcome = timeline.apply_rework(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'rework', body['id'], body)
    return {'revision': revision + 1, 'duplicate': False, **outcome}
