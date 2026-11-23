import re

from . import archives, comparison, intake, rules, wafers, workbench
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


def dispatch(database, method, path, body):
    for verb, pattern, write, handle in PATTERNS:
        if verb == method and (match := pattern.fullmatch(path)):
            with database.transaction(write=write) as conn:
                return handle(conn, body, *match.groups())
    raise DomainError('route', '找不到此接口', 404)
