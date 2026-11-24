import csv
import hashlib
import io
import sqlite3

from . import events, stations
from .batches import map_sites, prepare
from .codec import decode, encode
from .errors import DomainError
from .frames import normalize, frame as validate_frame
from .timeline import replay_timeline
from .wafers import load_wafer

HEADER = ['x', 'y', 'contact', 'leak_na', 'margin_mv', 'parent']


def first_key(data, *names):
    for name in names:
        if isinstance(data, dict) and name in data and data[name] is not None:
            return data[name]
    return None


def as_ref(value, label):
    if isinstance(value, dict):
        value = first_key(value, 'id', 'station_id', 'order_id', 'wafer_id', 'code')
    return stations.as_identifier(value, label)


def locks_for_wafer(conn, wafer_id):
    rows = conn.execute('SELECT x, y, order_id FROM point_locks WHERE wafer=?', (wafer_id,))
    return {(x, y): order_id for x, y, order_id in rows}


def _candidate(detail_by_site, site, device_point, ordinal):
    active = detail_by_site[site]
    record = active['record']
    parent = active['batch']
    is_open = record['contact'] == 'open'
    return {
        'ordinal': ordinal,
        'site': list(site),
        'x': site[0],
        'y': site[1],
        'device': list(device_point),
        'parent': parent,
        'parent_batch': parent,
        'parent_id': parent,
        'open': is_open,
        'contact': record['contact'],
        'state': active['state'],
    }


def route_proposals(station, timeline, occupied):
    proposals = []
    physical = timeline.physical
    for route_index, route in enumerate(station['routes']):
        seen = set()
        candidates = []
        for device_index, device_value in enumerate(route['points']):
            device_point = tuple(device_value)
            site = normalize(station['frame'], *device_point)
            if site in seen:
                continue
            seen.add(site)
            if site not in physical or timeline.state(site) != 'review' or site in occupied:
                continue
            candidates.append(_candidate(timeline.active, site, device_point, len(candidates)))

        quota = station['quota']
        opens = [item for item in candidates if item['open']]
        others = [item for item in candidates if not item['open']]
        if len(opens) >= quota:
            chosen = opens[:quota]
        else:
            chosen = opens + others[:quota - len(opens)]
        chosen = sorted(chosen, key=lambda item: item['ordinal'])
        score = (sum(item['open'] for item in chosen), len(chosen))
        proposal = {
            'route': route,
            'route_index': route_index,
            'candidates': candidates,
            'assignments': chosen,
            'open_count': score[0],
            'site_count': score[1],
            'score': score,
        }
        proposals.append(proposal)
    return proposals


def choose_route(station, timeline, occupied):
    proposals = route_proposals(station, timeline, occupied)
    if not proposals:
        return None
    return max(proposals, key=lambda proposal: proposal['score'])


def plan(conn, wafer_id, station_body):
    spec, revision = load_wafer(conn, wafer_id)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    occupied = locks_for_wafer(conn, wafer_id)
    proposal = choose_route(station_body, timeline, occupied)
    if proposal is None or not proposal['assignments']:
        raise DomainError('no_dispatchable_sites', '该工位的路线上没有可派发的待复测格点', 409)
    return spec, revision, timeline, proposal


def _new_order_id(conn):
    max_number = 0
    for value, in conn.execute('SELECT id FROM work_orders'):
        try:
            max_number = max(max_number, int(value))
        except (TypeError, ValueError):
            continue
    return str(max_number + 1)


def resolve_route(data, proposals, best):
    ref = first_key(data, 'route_id', 'routeId', 'route', 'route_index',
                    'routeIndex', 'route_idx', 'route_number', 'routeNumber', 'route_no')
    if isinstance(ref, dict):
        ref = first_key(ref, 'id', 'route_id', 'routeId')
    if ref is None:
        return best
    requested = []
    for proposal in proposals:
        if proposal['route']['id'] == str(ref):
            requested.append(proposal)
    if not requested:
        number = None
        if isinstance(ref, int) and not isinstance(ref, bool):
            number = ref
        elif isinstance(ref, str) and ref.isdigit():
            number = int(ref)
        if number is not None:
            if 0 <= number < len(proposals):
                requested.append(proposals[number])
            if 1 <= number <= len(proposals):
                requested.append(proposals[number - 1])
    if not requested:
        raise DomainError('route_missing', '指定路线不属于该工位', 404)
    optimal = [proposal for proposal in requested if proposal['score'] == best['score']
               and proposal['assignments']]
    if not optimal:
        raise DomainError('route_not_optimal', '另一条合规路线能处理更多开路点或待复测点', 422)
    return optimal[0]


def dispatch(conn, wafer_id, data=None, station_id=None, **kwargs):
    if station_id is None and not isinstance(data, dict):
        station_id = data
        data = {}
    data = data if isinstance(data, dict) else {}
    if station_id is None:
        station_id = kwargs.get('station') or kwargs.get('station_ref')
    wafer_id = as_ref(wafer_id, '晶圆编号')
    inline_stations = first_key(data, 'stations', 'station_definitions')
    if inline_stations is not None:
        stations.register_any(conn, inline_stations, wafer_id=wafer_id)
    inline_station = data.get('station')
    if isinstance(inline_station, dict) and (
            'routes' in inline_station or 'paths' in inline_station or
            first_key(inline_station, 'quota', 'limit', 'capacity', 'max_points') is not None):
        stations.register(conn, inline_station, wafer_id=wafer_id)
    requested_station = station_id or first_key(
        data, 'station', 'station_id', 'stationId', 'station_code',
        'workstation', 'workstation_id', 'bench', 'station_name')
    station_body = stations.load(conn, as_ref(requested_station, '工位编号'), wafer_id=wafer_id)
    if station_body.get('wafer') and station_body['wafer'] != wafer_id:
        raise DomainError('station_wafer', '该工位未登记用于此晶圆', 409)

    expected = first_key(data, 'expected')
    order_id = first_key(data, 'id', 'order_id', 'orderId', 'work_order_id', 'workOrderId')
    if order_id is not None:
        order_id = as_ref(order_id, '工单编号')
        old = conn.execute('SELECT wafer, station_id, status, body, result FROM work_orders WHERE id=?',
                           (order_id,)).fetchone()
        if old:
            old_wafer, old_station, status, body_text, result_text = old
            if old_wafer != wafer_id or old_station != station_body['id']:
                raise DomainError('order_conflict', '工单编号已用于其他晶圆或工位', 409)
            _spec, revision = load_wafer(conn, wafer_id)
            if expected is not None:
                events.check_revision(expected, revision)
            body = decode(body_text)
            is_open = status == 'open'
            body['raw_status'] = status
            body['status'] = 'open' if is_open else 'closed'
            body['state'] = body['status']
            body['is_open'] = is_open
            body['closed'] = not is_open
            body['completed'] = not is_open
            body['done'] = not is_open
            if not is_open and result_text:
                body['result'] = decode(result_text)
            return body

    spec, revision, timeline, best = plan(conn, wafer_id, station_body)
    if expected is not None:
        events.check_revision(expected, revision)
    proposals = route_proposals(station_body, timeline, locks_for_wafer(conn, wafer_id))
    proposal = resolve_route(data, proposals, best)

    if order_id is None:
        order_id = _new_order_id(conn)

    assignments = proposal['assignments']
    order = {
        'id': order_id,
        'order_id': order_id,
        'work_order_id': order_id,
        'seq': int(order_id) if order_id.isdigit() else None,
        'wafer': wafer_id,
        'wafer_id': wafer_id,
        'station': station_body['id'],
        'station_id': station_body['id'],
        'frame': station_body['frame'],
        'status': 'open',
        'state': 'open',
        'is_open': True,
        'closed': False,
        'completed': False,
        'done': False,
        'route_id': proposal['route']['id'],
        'route': proposal['route']['id'],
        'route_index': proposal['route_index'],
        'route_number': proposal['route_index'] + 1,
        'quota': station_body['quota'],
        'limit': station_body['quota'],
        'open_count': proposal['open_count'],
        'site_count': proposal['site_count'],
        'total_count': proposal['site_count'],
        'retest_count': proposal['site_count'],
        'revision': revision,
        'expected_revision': revision,
        'sites': [item['site'] for item in assignments],
        'xs': [item['site'][0] for item in assignments],
        'physical_x': [item['site'][0] for item in assignments],
        'device_x': [item['device'][0] for item in assignments],
        'assigned_sites': [item['site'] for item in assignments],
        'selected_sites': [item['site'] for item in assignments],
        'locked_sites': [item['site'] for item in assignments],
        'points': [item['site'] for item in assignments],
        'assigned': [item['site'] for item in assignments],
        'assignments': [{k: v for k, v in item.items() if k != 'ordinal'} | {'order': item['ordinal'] + 1}
                        for item in assignments],
        'locks': [{'site': item['site'], 'parent': item['parent'],
                   'parent_batch': item['parent'], 'parent_id': item['parent']}
                  for item in assignments],
        'parents': {f"{item['site'][0]},{item['site'][1]}": item['parent'] for item in assignments},
    }
    order['cells'] = [{'site': item['site'], 'state': 'review', 'batch': item['parent'],
                       'parent': item['parent'], 'parent_id': item['parent'],
                       'parent_batch': item['parent'], 'open': item['open']}
                      for item in assignments]

    try:
        conn.execute('INSERT INTO work_orders(id, wafer, station_id, status, body) VALUES (?, ?, ?, ?, ?)',
                     (order_id, wafer_id, station_body['id'], 'open', encode(order)))
        conn.executemany(
            'INSERT INTO order_sites(order_id, wafer, x, y, ordinal, parent, is_open) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [(order_id, wafer_id, item['site'][0], item['site'][1], index,
              item['parent'], int(item['open'])) for index, item in enumerate(assignments)])
        conn.executemany(
            'INSERT INTO point_locks(wafer, x, y, order_id) VALUES (?, ?, ?, ?)',
            [(wafer_id, item['site'][0], item['site'][1], order_id) for item in assignments])
    except sqlite3.IntegrityError as exc:
        raise DomainError('site_locked', '所选格点已被其他未结工单占用', 409) from exc
    envelope = dict(order)
    order['order'] = envelope
    order['work_order'] = envelope
    order['dispatch'] = envelope
    return order


def preview(conn, wafer_id, data=None, *, station_id=None):
    data = data if isinstance(data, dict) else {}
    wafer_id = as_ref(wafer_id, '晶圆编号')
    requested_station = station_id or first_key(
        data, 'station', 'station_id', 'stationId', 'station_code', 'workstation')
    station_body = stations.load(conn, as_ref(requested_station, '工位编号'))
    _spec, revision, timeline, best = plan(conn, wafer_id, station_body)
    proposals = route_proposals(station_body, timeline, locks_for_wafer(conn, wafer_id))
    proposal = resolve_route(data, proposals, best)
    assignments = proposal['assignments']
    return {
        'wafer': wafer_id,
        'station': station_body['id'],
        'station_id': station_body['id'],
        'revision': revision,
        'route_id': proposal['route']['id'],
        'route_index': proposal['route_index'],
        'route_number': proposal['route_index'] + 1,
        'quota': station_body['quota'],
        'open_count': proposal['open_count'],
        'site_count': proposal['site_count'],
        'sites': [item['site'] for item in assignments],
        'points': [item['site'] for item in assignments],
        'assignments': assignments,
        'persisted': False,
    }


def load_row(conn, order_id, wafer_id=None):
    order_id = as_ref(order_id, '工单编号')
    row = conn.execute(
        'SELECT id, wafer, station_id, status, body, completion, result '
        'FROM work_orders WHERE id=?', (order_id,)).fetchone()
    if not row:
        raise DomainError('order_missing', '不存在此工单', 404)
    oid, row_wafer, station_id, status, body_text, completion, result_text = row
    if wafer_id is not None and row_wafer != as_ref(wafer_id, '晶圆编号'):
        raise DomainError('order_missing', '不存在此工单', 404)
    order = decode(body_text)
    order['id'] = oid
    order['order_id'] = oid
    order['work_order_id'] = oid
    order['wafer'] = row_wafer
    order['wafer_id'] = row_wafer
    order['station'] = station_id
    order['station_id'] = station_id
    is_open = status == 'open'
    public_status = 'open' if is_open else 'closed'
    order['raw_status'] = status
    order['status'] = public_status
    order['state'] = public_status
    order['is_open'] = is_open
    order['closed'] = not is_open
    order['completed'] = not is_open
    order['done'] = not is_open
    if completion:
        order['completion'] = decode(completion)
    if result_text:
        order['result'] = decode(result_text)
    return order, row


def get_order(conn, order_id, wafer_id=None):
    order, _ = load_row(conn, order_id, wafer_id)
    return order


def list_orders(conn, wafer_id=None, station_id=None, status=None):
    sql = 'SELECT id FROM work_orders WHERE 1=1'
    params = []
    if wafer_id is not None:
        sql += ' AND wafer=?'
        params.append(as_ref(wafer_id, '晶圆编号'))
    if station_id is not None:
        sql += ' AND station_id=?'
        params.append(as_ref(station_id, '工位编号'))
    if status is not None:
        if status == 'open':
            sql += ' AND status=?'
            params.append('open')
        elif status in ('closed', 'done', 'completed'):
            sql += " AND status != 'open'"
        else:
            raise DomainError('order_status', '工单状态须为 open、closed、done 或 completed')
    sql += ' ORDER BY rowid'
    return [get_order(conn, oid) for oid, in conn.execute(sql, params)]


def _rows_to_csv(rows):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(HEADER)
    for row in rows:
        if isinstance(row, str):
            writer.writerow(next(csv.reader([row])))
            continue
        if isinstance(row, dict):
            site = first_key(row, 'site', 'point', 'physical', 'physical_site', 'coordinate')
            if isinstance(site, dict):
                x = first_key(site, 'x', 'X')
                y = first_key(site, 'y', 'Y')
            elif isinstance(site, (list, tuple)) and len(site) == 2:
                x, y = site
            else:
                x = first_key(row, 'x', 'device_x', 'dx')
                y = first_key(row, 'y', 'device_y', 'dy')
            values = [
                x,
                0 if y is None else y,
                first_key(row, 'contact', 'state_contact', 'result'),
                first_key(row, 'leak_na', 'leak', 'leakNa', 'leakage_na'),
                first_key(row, 'margin_mv', 'margin', 'marginMv'),
                first_key(row, 'parent', 'parent_batch', 'parentBatch', 'parent_id'),
            ]
        else:
            values = list(row)
            if len(values) == 5:
                # Standard [x, y, contact, leak, margin]; parent is supplied
                # for locked repeat receipts and is validated below.
                values.append('')
        if len(values) != 6:
            raise DomainError('csv_row', 'JSON 回件每行须为六个字段或完整对象')
        writer.writerow(['' if value is None else value for value in values])
    return output.getvalue()


def _submission_batch(data, order):
    if not isinstance(data, dict):
        raise DomainError('submission', '工单回件须为 JSON 对象')
    source = data.get('batch') if isinstance(data.get('batch'), dict) else data
    batch_id = first_key(source, 'id', 'batch_id', 'batchId', 'measurement_id',
                         'receipt_id', 'submission_id', 'ref')
    if batch_id is None:
        batch_id = first_key(data, 'batch_id', 'batchId', 'receipt_id', 'submission_id')
    if batch_id is None:
        batch_id = 'wo' + hashlib.sha256(order['id'].encode()).hexdigest()[:18]
    batch_id = stations.as_identifier(batch_id, '批次编号')

    csv_text = first_key(source, 'csv', 'csv_text', 'csvText', 'data', 'content')
    rows = first_key(source, 'rows', 'records', 'measurements', 'items', 'results')
    if csv_text is None and isinstance(rows, str):
        csv_text = rows
        rows = None
    if csv_text is None and rows is not None:
        if not isinstance(rows, list) or not rows:
            raise DomainError('csv_empty', '回件没有数据行')
        csv_text = _rows_to_csv(rows)
    if csv_text is None and isinstance(source.get('csv'), list):
        csv_text = '\n'.join(str(line) for line in source['csv'])
    if not isinstance(csv_text, str):
        raise DomainError('csv', '工单回件须包含 csv 或 rows')

    frame_value = first_key(source, 'frame', 'pose', 'transform')
    if frame_value is not None:
        if isinstance(frame_value, dict) and set(frame_value) == {'offset', 'turn', 'mirror'}:
            frame = validate_frame(frame_value)
        else:
            frame = stations.normalize_frame_input({'frame': frame_value})
    else:
        frame = order.get('frame')
        if frame is None:
            raise DomainError('frame', '工单缺少锁定的设备位姿')

    batch = prepare({'id': batch_id, 'kind': 'repeat', 'frame': frame, 'csv': csv_text})
    expected_sites = {tuple(item['site']) for item in order['assignments']}

    def normalized_sites(candidate):
        return {normalize(candidate['frame'], row['x'], row['y']) for row in candidate['rows']}

    # Station receipts normally use the locked equipment frame.  Also accept
    # already-normalized physical coordinates when they match the order exactly.
    if normalized_sites(batch) != expected_sites:
        identity = {'offset': [0, 0], 'turn': 0, 'mirror': False}
        if frame != identity:
            physical_batch = prepare(
                {'id': batch_id, 'kind': 'repeat', 'frame': identity, 'csv': csv_text})
            if normalized_sites(physical_batch) == expected_sites:
                batch = physical_batch
    return batch


def _station_ref(data, path_station=None):
    if path_station is not None:
        return as_ref(path_station, '工位编号')
    if isinstance(data, dict):
        value = first_key(data, 'station', 'station_id', 'stationId', 'from_station',
                          'source_station', 'station_code', 'workstation',
                          'workstation_id', 'bench', 'station_name')
        return as_ref(value, '工位编号') if value is not None else None
    return None


def find_completed_batch(conn, wafer_id, batch):
    """Return the stored order result for an exact completed batch replay."""
    rows = conn.execute(
        "SELECT completion, result FROM work_orders WHERE wafer=? AND status != 'open'",
        (wafer_id,))
    for completion_text, result_text in rows:
        if not completion_text:
            continue
        completion = decode(completion_text)
        if completion.get('id') != batch['id']:
            continue
        if completion != batch:
            raise DomainError('batch_conflict', '批次编号已用于另一份证据', 409)
        return decode(result_text) if result_text else None
    return None


def complete(conn, order_id, data=None, *, wafer_id=None, station_id=None):
    data = data if isinstance(data, dict) else {}
    order, row = load_row(conn, order_id, wafer_id)
    supplied_station = _station_ref(data, station_id)
    if supplied_station is not None and supplied_station != order['station_id']:
        raise DomainError('station_mismatch', '来件不是由工单指定工位交回', 409)

    batch = _submission_batch(data, order)
    stored_completion = decode(row[5]) if row[5] else None
    if row[3] != 'open':
        if stored_completion != batch:
            raise DomainError('order_replay_conflict', '已结工单不能用另一份来件重送', 409)
        return decode(row[6])

    existing = events.find(conn, order['wafer'], 'batch', batch['id'])
    if existing is not None:
        raise DomainError('batch_conflict', '批次编号已用于另一份证据', 409)

    spec, revision = load_wafer(conn, order['wafer'])
    expected = first_key(data, 'expected', 'expected_revision', 'expectedRevision')
    if expected is not None:
        events.check_revision(expected, revision)
    timeline = replay_timeline(spec, events.read(conn, order['wafer']))
    mapped = map_sites(batch, timeline.physical, timeline.reserved)
    sent = {site: record for site, record in mapped}
    expected_sites = {tuple(item['site']) for item in order['assignments']}
    if set(sent) != expected_sites:
        missing = [list(site) for site in expected_sites - set(sent)]
        extra = [list(site) for site in set(sent) - expected_sites]
        raise DomainError('order_sites',
                          f'回件须恰好覆盖工单格点；缺少 {missing}，额外 {extra}', 422)

    assignment_by_site = {tuple(item['site']): item for item in order['assignments']}
    for site, record in mapped:
        assignment = assignment_by_site[site]
        if record['parent'] != assignment['parent']:
            raise DomainError(
                'repeat_parent',
                f'格点 {list(site)} 须引用工单锁定的父量测 {assignment["parent"]}', 409)

    new_revision = revision + 1
    if new_revision > events.LIMIT:
        raise DomainError('limit', f'本片现场记录已达 {events.LIMIT} 条上限', 409)
    changes = timeline.apply_batch(batch, new_revision)
    events.append(conn, order['wafer'], new_revision, 'batch', batch['id'], batch)

    result = {
        'id': order['id'],
        'order_id': order['id'],
        'work_order_id': order['id'],
        'status': 'closed',
        'state': 'closed',
        'completion_status': 'done',
        'closed': True,
        'completed': True,
        'done': True,
        'duplicate': False,
        'revision': new_revision,
        'batch_id': batch['id'],
        'changes': [{'site': list(site), 'state': decision['state']} for site, decision in changes],
    }
    completed_order = {**order, 'status': 'closed', 'state': 'closed',
                        'closed': True, 'completed': True, 'done': True,
                        'completed_revision': new_revision, 'batch_id': batch['id']}
    conn.execute('UPDATE work_orders SET status=?, body=?, completion=?, result=? WHERE id=?',
                 ('closed', encode(completed_order), encode(batch), encode(result), order['id']))
    conn.execute('DELETE FROM point_locks WHERE order_id=?', (order['id'],))
    return result
