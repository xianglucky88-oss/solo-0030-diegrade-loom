import csv
import io
import re
from urllib.parse import unquote

from . import archives, comparison, intake, rules, wafers, workbench, zones
from .errors import DomainError
from .partition import testable, zones_of
from .sorter import as_csv
from .validation import integer


def zones_describe(spec):
    sites = testable(spec)
    return {'zoned': bool(zones_of(spec)), 'testable': len(sites),
            'zones': [{'id': zone['id'], 'sites': zone['sites'], 'count': len(zone['sites'])}
                      for zone in zones_of(spec)]}

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
    spec, _ = wafers.load_wafer(conn, wafer)
    if spec.get('zones'):
        raise DomainError('zoned_wafer', '该晶圆已启用分区交付，整片签出已停用，请取交接清册', 409)
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


@route('GET', r'/wafers/([^/]+)/zones')
def zone_list(conn, _, wafer):
    spec, revision = wafers.load_wafer(conn, wafer)
    return {'wafer': spec['id'], 'revision': revision, **zones_describe(spec)}


@route('POST', r'/wafers/([^/]+)/zones', write=True)
def zone_enable(conn, body, wafer):
    spec, revision = wafers.load_wafer(conn, wafer)
    return zones.enable_partition(conn, spec, revision, body)


@route('POST', r'/wafers/([^/]+)/zone-plans', write=True)
def zone_plan(conn, body, wafer):
    outcome = zones.prepare(conn, wafer, body)
    return {'zone': outcome['zone'], 'seq': outcome['plan']['payload']['seq'],
            'sha256': outcome['plan']['sha256'], 'duplicate': outcome['duplicate'],
            'sites': len(outcome['plan']['payload']['entries'])}


@route('GET', r'/wafers/([^/]+)/zone-plans/([^/]+)/([0-9]{1,6})')
def zone_plan_view(conn, _, wafer, zone, seq):
    return zones.review(conn, wafer, unquote(zone), int(seq))


@route('POST', r'/wafers/([^/]+)/zone-releases', write=True)
def zone_release(conn, body, wafer):
    outcome = zones.release(conn, wafer, body)
    frozen = outcome['release']
    return {'zone': outcome['zone'], 'sha256': frozen['sha256'], 'duplicate': outcome['duplicate'],
            'rows': len(frozen['payload']['rows'])}


@route('GET', r'/wafers/([^/]+)/zone-releases/([^/]+)/(manifest|sorter)')
def zone_release_asset(conn, _, wafer, zone, action):
    zone = unquote(zone)
    release = zones.delivered(conn, wafer, zone)
    if action == 'manifest':
        return release, 'application/json; charset=utf-8', f'{wafer}-{zone}.json'
    return as_csv(release['payload']['rows']), 'text/csv; charset=utf-8', f'{wafer}-{zone}.csv'


@route('GET', r'/wafers/([^/]+)/zone-ledger')
def zone_ledger(conn, _, wafer):
    return zones.ledger(conn, wafer)


@route('GET', r'/wafers/([^/]+)/zone-ledger/verify')
def zone_ledger_replay(conn, _, wafer):
    spec, _ = wafers.load_wafer(conn, wafer)
    return zones.verify_ledger(spec, zones.ledger(conn, wafer))


@route('GET', r'/wafers/([^/]+)/zone-ledger/manifest')
def zone_ledger_manifest(conn, _, wafer):
    spec, revision = wafers.load_wafer(conn, wafer)
    return zones.ledger(conn, wafer), 'application/json; charset=utf-8', f'{wafer}-ledger-r{revision}.json'


@route('GET', r'/wafers/([^/]+)/zone-ledger/csv')
def zone_ledger_sorter(conn, _, wafer):
    spec, revision = wafers.load_wafer(conn, wafer)
    rows = zones.ledger_rows(zones.ledger(conn, wafer))
    columns = ['x', 'y', 'zone', 'bin', 'batch', 'release']
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue(), 'text/csv; charset=utf-8', f'{wafer}-ledger-r{revision}.csv'


@route('POST', r'/wafers/([^/]+)/zone-ledger/verify', write=False)
def zone_ledger_verify(conn, body, wafer):
    spec, _ = wafers.load_wafer(conn, wafer)
    if not isinstance(body, dict):
        raise DomainError('ledger', '请提交交接清册', 422)
    return zones.verify_ledger(spec, body)


@route('POST', r'/zone-ledger/verify')
def zone_ledger_offline(conn, body, _):
    if not isinstance(body, dict):
        raise DomainError('ledger', '请提交交接清册', 422)
    return zones.verify_ledger_offline(body)


def dispatch(database, method, path, body):
    for verb, pattern, write, handle in PATTERNS:
        if verb == method and (match := pattern.fullmatch(path)):
            with database.transaction(write=write) as conn:
                return handle(conn, body, *match.groups())
    raise DomainError('route', '找不到此接口', 404)

