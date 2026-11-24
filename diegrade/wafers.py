from .codec import decode, encode
from .errors import DomainError
from .geometry import build_mask, order
from .partition import normalize
from .rules import load
from .validation import identifier, integer

REGISTER_FIELDS = ('id', 'spans', 'reserved', 'rule', 'rule_version')


def _zones_payload(data):
    for key in ('zones', 'partition'):
        if key in data:
            return data[key]
    return None


def register(conn, data):
    if not isinstance(data, dict) or not set(REGISTER_FIELDS) <= set(data) \
            or set(data) - set(REGISTER_FIELDS) - {'zones', 'partition'}:
        raise DomainError('fields', '请求字段应恰为：' + '、'.join(REGISTER_FIELDS) + '，可附 zones')
    wafer_id = identifier(data['id'])
    physical, reserved = build_mask(data['spans'], data['reserved'])
    rule = load(conn, identifier(data['rule']), integer(data['rule_version'], 1, 9999, '规则版本'))
    spec = {'id': wafer_id, 'spans': sorted(data['spans']), 'reserved': [list(p) for p in order(reserved)], 'rule': rule}
    zones = normalize(_zones_payload(data), physical, reserved)
    if zones:
        spec['zones'] = zones
    row = conn.execute('SELECT body FROM wafers WHERE id=?', (wafer_id,)).fetchone()
    if row and decode(row[0]) != spec:
        raise DomainError('wafer_conflict', '晶圆形状、规则与分区已锁定', 409)
    if not row:
        conn.execute('INSERT INTO wafers(id, body) VALUES (?, ?)', (wafer_id, encode(spec)))
    return spec


def save(conn, spec):
    conn.execute('UPDATE wafers SET body=? WHERE id=?', (encode(spec), spec['id']))
    return spec


def load_wafer(conn, wafer_id):
    row = conn.execute('SELECT body, revision FROM wafers WHERE id=?', (identifier(wafer_id),)).fetchone()
    if not row:
        raise DomainError('wafer_missing', '不存在此晶圆', 404)
    return decode(row[0]), row[1]


def list_wafers(conn):
    return [{'id': name, 'revision': revision, 'zoned': bool(decode(body).get('zones'))}
            for name, revision, body in conn.execute('SELECT id, revision, body FROM wafers ORDER BY id')]
