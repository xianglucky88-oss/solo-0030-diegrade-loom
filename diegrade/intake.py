from . import events
from .batches import prepare
from .errors import DomainError
from .geometry import point
from .timeline import replay_timeline
from .validation import fields, identifier, reason, stations
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


def open_round(conn, wafer_id, data):
    """复核员为一个当前待复测格点开立有独立编号的两站复测轮次。"""
    fields(data, 'expected', 'id', 'site', 'stations', 'reason')
    site = point(data['site'])
    round_id = identifier(data['id'])
    body = {'id': round_id, 'site': list(site), 'stations': stations(data['stations']),
            'reason': reason(data['reason'])}
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    old = events.find(conn, wafer_id, 'round', round_id)
    if old:
        if old != body:
            raise DomainError('round_conflict', '轮次编号已用于另一条现场记录', 409)
        return {'revision': revision, 'duplicate': True}
    timeline.apply_round(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'round', round_id, body)
    return {'revision': revision + 1, 'duplicate': False, 'parent': timeline.rounds[round_id]['parent']}


def submit(conn, wafer_id, round_id, data):
    """登记某工位送回的一份复测来件；同一工位只接受一份。"""
    fields(data, 'expected', 'station', 'batch')
    round_id = identifier(round_id)
    station = identifier(data['station'])
    batch = prepare(data['batch'])
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    body = {'round': round_id, 'station': station, 'batch': batch}
    found = events.find(conn, wafer_id, 'submission', submission_ref(round_id, station))
    if found is not None:
        if found != body:
            raise DomainError('submission_conflict', '该工位已送回证据，不能覆盖', 409)
        return {'revision': revision, 'duplicate': True}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline.apply_submission(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'submission', submission_ref(round_id, station), body)
    round_view = next(view for view in timeline.round_views() if view['id'] == round_id)
    return {'revision': revision + 1, 'duplicate': False,
            'round': {'id': round_id, 'status': round_view['status'],
                      'received': [item['station'] for item in round_view['submissions']]}}


def decide(conn, wafer_id, round_id, data):
    """复核员按当前代次提交有编号和事由的裁决，选中一份能终判的来件。"""
    fields(data, 'expected', 'id', 'station', 'reason')
    verdict_id = identifier(data['id'])
    round_id = identifier(round_id)
    station = identifier(data['station'])
    body = {'id': verdict_id, 'round': round_id, 'station': station, 'reason': reason(data['reason'])}
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    old = events.find(conn, wafer_id, 'verdict', verdict_id)
    if old is not None:
        if old != body:
            raise DomainError('verdict_conflict', '裁决编号已用于另一条现场记录', 409)
        return {'revision': revision, 'duplicate': True}
    timeline.apply_verdict(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'verdict', verdict_id, body)
    found = timeline.rounds[round_id]
    site = tuple(found['body']['site'])
    return {'revision': revision + 1, 'duplicate': False, 'round': round_id, 'station': station,
            'cell': timeline.cell(site)}


def void_round(conn, wafer_id, round_id, data):
    """两站都开路时作废本轮；父量测保留，可用同一实际格点另开新一轮。"""
    fields(data, 'expected', 'id', 'reason')
    void_id = identifier(data['id'])
    round_id = identifier(round_id)
    body = {'id': void_id, 'round': round_id, 'reason': reason(data['reason'])}
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    old = events.find(conn, wafer_id, 'void', void_id)
    if old is not None:
        if old != body:
            raise DomainError('void_conflict', '作废编号已用于另一条现场记录', 409)
        return {'revision': revision, 'duplicate': True}
    timeline.apply_void(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'void', void_id, body)
    return {'revision': revision + 1, 'duplicate': False, 'round': round_id}


def submission_ref(round_id, station):
    return f'{round_id}/{station}'
