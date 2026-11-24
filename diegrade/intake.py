from . import events, workorders
from .batches import map_sites, prepare
from .errors import DomainError
from .geometry import point
from .timeline import replay_timeline
from .validation import fields, identifier, reason
from .wafers import load_wafer


def current(conn, wafer_id):
    spec, revision = load_wafer(conn, wafer_id)
    return replay_timeline(spec, events.read(conn, wafer_id)), revision


def intake(conn, wafer_id, data, write, order_id=None, station_id=None):
    if isinstance(data, dict):
        order_id = order_id or workorders.first_key(
            data, 'order_id', 'work_order_id', 'orderId', 'workOrderId', 'dispatch_id')
        nested = data.get('batch') if isinstance(data.get('batch'), dict) else {}
        if order_id is None:
            order_id = workorders.first_key(
                nested, 'order_id', 'work_order_id', 'orderId', 'workOrderId', 'dispatch_id')
        if order_id is not None and write:
            station_id = station_id or workorders.first_key(
                data, 'station', 'station_id', 'stationId', 'from_station',
                'station_code', 'workstation', 'workstation_id', 'bench')
            if station_id is None:
                station_id = workorders.first_key(
                    nested, 'station', 'station_id', 'stationId', 'from_station',
                    'station_code', 'workstation', 'workstation_id', 'bench')
            return workorders.complete(
                conn, order_id, data, wafer_id=wafer_id, station_id=station_id)

    fields(data, 'expected', 'batch')
    batch = prepare(data['batch'])
    spec, revision = load_wafer(conn, wafer_id)
    order_replay = workorders.find_completed_batch(conn, wafer_id, batch)
    if order_replay is not None:
        return order_replay
    duplicate = events.find(conn, wafer_id, 'batch', batch['id'])
    events.check_revision(data['expected'], revision)
    if duplicate is not None:
        if duplicate != batch:
            raise DomainError('batch_conflict', '批次编号已用于另一份证据', 409)
        return {'revision': revision, 'duplicate': True, 'changes': []}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    mapped = map_sites(batch, timeline.physical, timeline.reserved)
    locks = workorders.locks_for_wafer(conn, wafer_id)
    for site, _record in mapped:
        if site in locks:
            raise DomainError(
                'site_locked',
                f'格点 {list(site)} 已由未结工单 {locks[site]} 锁定，须经该工单回件', 409)
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
