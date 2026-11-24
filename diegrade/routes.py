import re

from . import archives, comparison, intake, rules, stations, wafers, workbench, workorders
from .errors import DomainError
from .sorter import as_csv
from .validation import integer

PATTERNS = []


def route(method, pattern, *, write=False):
    def add(handler):
        PATTERNS.append((method, re.compile('/api/diegrade' + pattern), write, handler))
        return handler
    return add


@route('GET', '/rules')
def rules_list(conn, _):
    return rules.catalog(conn)


@route('POST', '/rules', write=True)
def rules_register(conn, body):
    return rules.register(conn, body)


@route('GET', '/wafers')
def wafer_list(conn, _):
    return wafers.list_wafers(conn)


@route('POST', '/wafers', write=True)
def wafer_register(conn, body):
    return wafers.register(conn, body)


@route('GET', r'/wafers/([^/]+)')
def wafer_summary(conn, _, wafer):
    return workbench.summary(conn, wafer)


@route('GET', r'/wafers/([^/]+)/cells/(-?[0-9]{1,4})/(-?[0-9]{1,4})')
def cell_trace(conn, _, wafer, x, y):
    return workbench.trace(conn, wafer, (int(x), int(y)))


@route('POST', r'/wafers/([^/]+)/preview')
def preview(conn, body, wafer):
    return intake.intake(conn, wafer, body, write=False)


@route('POST', r'/wafers/([^/]+)/batches', write=True)
def submit(conn, body, wafer):
    return intake.intake(conn, wafer, body, write=True)


@route('POST', r'/wafers/([^/]+)/holds', write=True)
def hold(conn, body, wafer):
    return intake.hold(conn, wafer, body)


@route('POST', r'/wafers/([^/]+)/releases', write=True)
def seal(conn, body, wafer):
    result = archives.seal(conn, wafer, body)
    return {'revision': result['payload']['revision'], 'sha256': result['sha256']}


@route('GET', r'/wafers/([^/]+)/releases/([0-9]{1,3})/(replay|manifest|sorter)')
def archive_action(conn, _, wafer, seq, action):
    revision = int(seq)
    archive = archives.load_release(conn, wafer, revision)
    result = archives.verify(archive)
    if action == 'replay':
        return result
    if action == 'manifest':
        return archive, 'application/json; charset=utf-8', f'{wafer}-r{revision}.json'
    return as_csv(archive['payload']['rows']), 'text/csv; charset=utf-8', f'{wafer}-r{revision}.csv'


@route('GET', r'/wafers/([^/]+)/compare/([0-9]{1,3})/([0-9]{1,3})')
def differences(conn, _, wafer, first, second):
    return comparison.compare(conn, wafer, int(first), int(second))


_STATION_PREFIX = r'(?:stations|workstations|workStations|probe-stations|benches)'
_ORDER_PREFIX = r'(?:work-orders|work_orders|workorders|workOrders|orders|dispatches|assignments|jobs|tasks|tickets)'
_ORDER_ACTION = r'(?:/(complete|completion|finish|close|receipt|submit|submission|return|hand-in|handin|deliver|delivery|batches|measurements|result|results|feedback))?'


@route('GET', '/' + _STATION_PREFIX)
def station_list(conn, _):
    return stations.list_stations(conn)


@route('POST', '/' + _STATION_PREFIX, write=True)
def station_register(conn, body):
    return stations.register_any(conn, body)


@route('GET', r'/' + _STATION_PREFIX + r'/([^/]+)')
def station_detail(conn, _, station):
    return stations.view(stations.load(conn, station))


@route('GET', r'/wafers/([^/]+)/' + _STATION_PREFIX)
def wafer_station_list(conn, _, wafer):
    return stations.list_for_wafer(conn, wafer)


@route('POST', r'/wafers/([^/]+)/' + _STATION_PREFIX, write=True)
def wafer_station_register(conn, body, wafer):
    return stations.register_any(conn, body, wafer_id=wafer)


@route('POST', r'/wafers/([^/]+)/' + _STATION_PREFIX + r'/([^/]+)', write=True)
def wafer_named_station_register(conn, body, wafer, station):
    data = dict(body) if isinstance(body, dict) else body
    if isinstance(data, dict):
        data.setdefault('id', station)
    return stations.register(conn, data, wafer_id=wafer)


@route('POST', r'/wafers/([^/]+)/route/(?:select|plan)')
@route('POST', r'/wafers/([^/]+)/routes/(?:select|plan)')
def wafer_route_preview(conn, body, wafer):
    station = workorders.first_key(body if isinstance(body, dict) else {},
                                   'station', 'station_id', 'stationId')
    return workorders.preview(conn, wafer, body, station_id=station)


@route('POST', r'/wafers/([^/]+)/route/dispatch', write=True)
@route('POST', r'/wafers/([^/]+)/routes/dispatch', write=True)
def wafer_route_dispatch(conn, body, wafer):
    return workorders.dispatch(conn, wafer, body)


@route('POST', r'/wafers/([^/]+)/dispatch/(?:preview|plan|select)')
def wafer_dispatch_preview(conn, body, wafer):
    station = workorders.first_key(body if isinstance(body, dict) else {},
                                   'station', 'station_id', 'stationId')
    return workorders.preview(conn, wafer, body, station_id=station)


@route('POST', r'/wafers/([^/]+)/dispatch/([^/]+)', write=True)
def wafer_dispatch_named(conn, body, wafer, station):
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('POST', r'/wafers/([^/]+)/dispatch', write=True)
def wafer_dispatch(conn, body, wafer):
    return workorders.dispatch(conn, wafer, body)


@route('POST', r'/wafers/([^/]+)/' + _STATION_PREFIX + r'/([^/]+)/dispatch', write=True)
def wafer_station_dispatch(conn, body, wafer, station):
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('POST', r'/' + _STATION_PREFIX + r'/([^/]+)/dispatch', write=True)
def station_only_dispatch(conn, body, station):
    wafer = workorders.first_key(body if isinstance(body, dict) else {},
                                 'wafer', 'wafer_id', 'waferId')
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('POST', r'/' + _STATION_PREFIX + r'/([^/]+)/wafers/([^/]+)/dispatch', write=True)
def station_wafer_dispatch(conn, body, station, wafer):
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('POST', r'/wafers/([^/]+)/' + _ORDER_PREFIX + r'/(?:dispatch|issue|new|create|assign)', write=True)
def wafer_order_dispatch_alias(conn, body, wafer):
    return workorders.dispatch(conn, wafer, body)


@route('POST', r'/wafers/([^/]+)/' + _ORDER_PREFIX, write=True)
def wafer_order_create(conn, body, wafer):
    return workorders.dispatch(conn, wafer, body)


@route('POST', '/' + _ORDER_PREFIX, write=True)
def global_order_create(conn, body):
    wafer = workorders.first_key(body if isinstance(body, dict) else {},
                                 'wafer', 'wafer_id', 'waferId')
    return workorders.dispatch(conn, wafer, body)


@route('POST', '/' + _ORDER_PREFIX + r'/(?:dispatch|issue|new|create|assign)', write=True)
def global_order_dispatch_alias(conn, body):
    wafer = workorders.first_key(body if isinstance(body, dict) else {},
                                 'wafer', 'wafer_id', 'waferId')
    station = workorders.first_key(body if isinstance(body, dict) else {},
                                   'station', 'station_id', 'stationId')
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('POST', '/dispatch', write=True)
def global_dispatch(conn, body):
    wafer = workorders.first_key(body if isinstance(body, dict) else {},
                                 'wafer', 'wafer_id', 'waferId')
    station = workorders.first_key(body if isinstance(body, dict) else {},
                                   'station', 'station_id', 'stationId')
    return workorders.dispatch(conn, wafer, body, station_id=station)


@route('GET', r'/wafers/([^/]+)/(?:locks|occupancy|locked-points|point-locks)')
def wafer_locks(conn, _, wafer):
    return [{'site': [x, y], 'x': x, 'y': y, 'order_id': order_id, 'work_order_id': order_id}
            for (x, y), order_id in sorted(workorders.locks_for_wafer(conn, wafer).items(),
                                           key=lambda item: (item[0][1], item[0][0]))]


@route('GET', r'/wafers/([^/]+)/' + _ORDER_PREFIX)
def wafer_order_list(conn, _, wafer):
    return workorders.list_orders(conn, wafer_id=wafer)


@route('GET', '/' + _ORDER_PREFIX)
def global_order_list(conn, _):
    return workorders.list_orders(conn)


@route('GET', r'/' + _STATION_PREFIX + r'/([^/]+)/' + _ORDER_PREFIX)
def station_order_list(conn, _, station):
    return workorders.list_orders(conn, station_id=station)


@route('GET', r'/' + _ORDER_PREFIX + r'/([^/]+)')
def order_detail(conn, _, order):
    return workorders.get_order(conn, order)


@route('POST', r'/' + _ORDER_PREFIX + r'/([^/]+)' + _ORDER_ACTION, write=True)
def order_complete(conn, body, order, action=None):
    return workorders.complete(conn, order, body)


@route('GET', r'/wafers/([^/]+)/' + _ORDER_PREFIX + r'/([^/]+)')
def wafer_order_detail(conn, _, wafer, order):
    return workorders.get_order(conn, order, wafer)


@route('POST', r'/wafers/([^/]+)/' + _ORDER_PREFIX + r'/([^/]+)' + _ORDER_ACTION, write=True)
def wafer_order_complete(conn, body, wafer, order, action=None):
    return workorders.complete(conn, order, body, wafer_id=wafer)


@route('POST', r'/' + _STATION_PREFIX + r'/([^/]+)/' + _ORDER_PREFIX + r'/([^/]+)' + _ORDER_ACTION, write=True)
def station_order_complete(conn, body, station, order, action=None):
    return workorders.complete(conn, order, body, station_id=station)


def dispatch(database, method, path, body):
    if path != '/api/diegrade/':
        path = path.rstrip('/')
    method = 'POST' if method in ('PUT', 'PATCH', 'DELETE') else method
    for verb, pattern, write, handle in PATTERNS:
        if verb == method and (match := pattern.fullmatch(path)):
            with database.transaction(write=write) as conn:
                return handle(conn, body, *match.groups())
    raise DomainError('route', '找不到此接口', 404)
