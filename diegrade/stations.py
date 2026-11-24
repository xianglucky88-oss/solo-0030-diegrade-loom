from .codec import decode, encode
from .errors import DomainError
from .frames import frame as validate_frame
from .validation import identifier, integer


def as_identifier(value, label='编号'):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return identifier(value)


def first_key(data, *names):
    for name in names:
        if name in data and data[name] is not None:
            return data[name]
    return None


def device_point(value):
    if isinstance(value, int) and not isinstance(value, bool):
        x, y = value, 0
    elif isinstance(value, dict):
        x = first_key(value, 'x', 'device_x', 'dx', 'X')
        y = first_key(value, 'y', 'device_y', 'dy', 'Y')
        if y is None:
            y = 0
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
    else:
        raise DomainError('route_point', '路线坐标须为 [x, y] 或包含 x、y 的对象')
    return tuple(integer(v, -2048, 2048, '路线设备坐标') for v in (x, y))


def route_points(route):
    if isinstance(route, dict):
        if first_key(route, 'points', 'coords', 'coordinates', 'sites', 'path',
                     'device', 'device_points', 'device_coordinates',
                     'waypoints', 'sequence') is not None:
            raw = first_key(route, 'points', 'coords', 'coordinates', 'sites', 'path',
                            'device', 'device_points', 'device_coordinates',
                            'waypoints', 'sequence')
        else:
            xs_key = next((key for key in ('x', 'xs', 'device_x', 'deviceX', 'X') if key in route), None)
            if xs_key is None:
                raise DomainError('route', '路线须包含坐标列表')
            xs = route[xs_key]
            ys = first_key(route, 'y', 'ys', 'device_y', 'deviceY', 'Y')
            if ys is None:
                ys = 0
            if not isinstance(xs, list):
                xs = [xs]
            if not isinstance(ys, list):
                ys = [ys] * len(xs)
            if len(ys) != len(xs):
                raise DomainError('route', '路线 x、y 坐标数量不一致')
            raw = zip(xs, ys)
    elif isinstance(route, (list, tuple)):
        raw = route
    else:
        raise DomainError('route', '每条路线须为坐标列表或路线对象')
    points = [device_point(value) for value in raw]
    if not 1 <= len(points) <= 100000:
        raise DomainError('route', '每条路线须包含 1–100000 个设备坐标')
    return points


def normalize_frame_input(data):
    explicit = first_key(data, 'frame', 'pose', 'transform', 'mapping', 'coordinate_frame')
    if explicit is None:
        source = data
    elif isinstance(explicit, dict):
        source = explicit
    elif isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        number = int(explicit)
        explicit = {'turn': number if 0 <= number <= 3 else None, 'rotation': None if 0 <= number <= 3 else number}
        source = explicit
    else:
        raise DomainError('frame', '设备位姿须为坐标变换对象或旋转整数')

    offset = first_key(source, 'offset', 'translation', 'origin', 'position')
    if offset is None:
        offset = [0, 0]
    mirror = first_key(source, 'mirror', 'mirror_x', 'flip_x', 'mirrored')
    turn = first_key(source, 'turn', 'turns', 'quarter_turns')
    if turn is None:
        rotation = first_key(source, 'rotation', 'rotate', 'angle', 'degrees')
        if rotation is not None:
            if isinstance(rotation, float) and rotation.is_integer():
                rotation = int(rotation)
            if not isinstance(rotation, int) or isinstance(rotation, bool) or rotation % 90:
                raise DomainError('frame', '旋转角须为 90 度的整数倍')
            turn = (rotation // 90) % 4
        else:
            turn = 0
    elif isinstance(turn, float) and turn.is_integer():
        turn = int(turn)
    elif isinstance(turn, int) and not isinstance(turn, bool) and not 0 <= turn <= 3:
        if turn % 90 == 0:
            turn = (turn // 90) % 4
    value = {'offset': offset, 'turn': turn, 'mirror': bool(mirror) if mirror is not None else False}
    return validate_frame(value)


def normalize_station(data, *, wafer_id=None):
    if not isinstance(data, dict):
        raise DomainError('station', '工位登记内容须为 JSON 对象')
    station_id = as_identifier(first_key(
        data, 'id', 'station_id', 'stationId', 'station', 'workstation_id',
        'workstationId', 'code', 'name'), '工位编号')
    quota_value = first_key(data, 'quota', 'point_quota', 'site_quota', 'limit', 'capacity',
                            'max_points', 'max_sites', 'point_limit', 'site_limit',
                            'max_cells', 'max_grid_points', 'max_points_per_order',
                            'max_sites_per_order', 'max_contacts', 'contact_limit',
                            'max_touch_points', 'max_touchpoints', 'max')
    if quota_value is None:
        raise DomainError('station_quota', '须登记工单可接触格点数上限')
    if isinstance(quota_value, dict):
        quota_value = first_key(quota_value, 'max', 'quota', 'limit', 'points', 'sites', 'value')
    quota = integer(quota_value, 1, 4096, '工单格点额度')
    raw_routes = first_key(data, 'routes', 'paths', 'available_routes', 'route_list',
                           'waypoints', 'tracks')
    if isinstance(raw_routes, dict):
        raw_routes = [({'id': route_id, **route_value} if isinstance(route_value, dict)
                       else {'id': route_id, 'points': route_value})
                      for route_id, route_value in raw_routes.items()]
    if not isinstance(raw_routes, list) or not 1 <= len(raw_routes) <= 4096:
        raise DomainError('station_routes', '工位须登记至少一条且不超过 4096 条路线')
    routes = []
    seen_route_ids = set()
    for index, raw_route in enumerate(raw_routes, 1):
        route_id = None
        if isinstance(raw_route, dict):
            raw_id = first_key(raw_route, 'id', 'route_id', 'routeId', 'code', 'name')
            if raw_id is not None:
                route_id = as_identifier(raw_id, '路线编号')
        route_id = route_id or f'R{index}'
        if route_id in seen_route_ids:
            raise DomainError('route_conflict', f'路线编号 {route_id} 重复')
        seen_route_ids.add(route_id)
        routes.append({'id': route_id, 'points': [list(point) for point in route_points(raw_route)]})
    body = {
        'id': station_id,
        'frame': normalize_frame_input(data),
        'quota': quota,
        'routes': routes,
    }
    scope = first_key(data, 'wafer', 'wafer_id', 'waferId') or wafer_id
    if scope is not None:
        body['wafer'] = as_identifier(scope, '晶圆编号')
    return body


def view(body):
    """Return canonical fields plus the common quota aliases used by clients."""
    return {**body, 'limit': body['quota'], 'capacity': body['quota'], 'max_points': body['quota']}


def register(conn, data, *, wafer_id=None):
    station = normalize_station(data, wafer_id=wafer_id)
    scope = station.get('wafer', '')
    row = conn.execute('SELECT body FROM stations WHERE scope=? AND id=?',
                       (scope, station['id'])).fetchone()
    if row:
        old = decode(row[0])
        if old != station:
            raise DomainError('station_conflict', '工位编号已用于另一份位姿或路线', 409)
        return view(old)
    conn.execute('INSERT INTO stations(scope, id, body) VALUES (?, ?, ?)',
                 (scope, station['id'], encode(station)))
    return view(station)


def register_any(conn, data, *, wafer_id=None):
    if isinstance(data, list):
        return [register(conn, item, wafer_id=wafer_id) for item in data]
    if isinstance(data, dict):
        if isinstance(data.get('stations'), list):
            return [register(conn, item, wafer_id=first_key(item, 'wafer', 'wafer_id') or wafer_id)
                    for item in data['stations']]
        if 'id' not in data and all(isinstance(value, dict) for value in data.values()):
            return [register(conn, {'id': key, **value}, wafer_id=wafer_id)
                    for key, value in data.items()]
    return register(conn, data, wafer_id=wafer_id)


def load(conn, station_id, wafer_id=None):
    station_id = as_identifier(station_id, '工位编号')
    row = None
    if wafer_id is not None:
        scope = as_identifier(wafer_id, '晶圆编号')
        row = conn.execute('SELECT body FROM stations WHERE scope=? AND id=?',
                           (scope, station_id)).fetchone()
    if row is None:
        row = conn.execute('SELECT body FROM stations WHERE scope=? AND id=?',
                           ('', station_id)).fetchone()
    if row is None and wafer_id is None:
        row = conn.execute(
            'SELECT body FROM stations WHERE id=? ORDER BY CASE scope WHEN ? THEN 0 ELSE 1 END, scope LIMIT 1',
            (station_id, '')).fetchone()
    if not row:
        raise DomainError('station_missing', '不存在此工位', 404)
    return decode(row[0])


def list_stations(conn):
    return [view(decode(body)) for body, in conn.execute('SELECT body FROM stations ORDER BY scope, id')]


def list_for_wafer(conn, wafer_id):
    scope = as_identifier(wafer_id, '晶圆编号')
    rows = conn.execute('SELECT scope, id, body FROM stations WHERE scope IN (?, ?)',
                        (scope, '')).fetchall()
    by_id = {}
    for row_scope, station_id, body in rows:
        if station_id not in by_id or row_scope == scope:
            by_id[station_id] = body
    return [view(decode(body)) for station_id, body in sorted(by_id.items())]
