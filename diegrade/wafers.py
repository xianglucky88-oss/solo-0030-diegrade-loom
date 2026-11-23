from .codec import decode, encode
from .errors import DomainError
from .geometry import build_mask, order
from .rules import load
from .validation import fields, identifier, integer


def register(conn, data):
    fields(data, 'id', 'spans', 'reserved', 'rule', 'rule_version')
    wafer_id = identifier(data['id'])
    _, reserved = build_mask(data['spans'], data['reserved'])
    rule = load(conn, identifier(data['rule']), integer(data['rule_version'], 1, 9999, '规则版本'))
    spec = {'id': wafer_id, 'spans': sorted(data['spans']), 'reserved': [list(p) for p in order(reserved)], 'rule': rule}
    row = conn.execute('SELECT body FROM wafers WHERE id=?', (wafer_id,)).fetchone()
    if row and decode(row[0]) != spec:
        raise DomainError('wafer_conflict', '晶圆形状与规则已锁定', 409)
    if not row:
        conn.execute('INSERT INTO wafers(id, body) VALUES (?, ?)', (wafer_id, encode(spec)))
    return spec


def load_wafer(conn, wafer_id):
    row = conn.execute('SELECT body, revision FROM wafers WHERE id=?', (identifier(wafer_id),)).fetchone()
    if not row:
        raise DomainError('wafer_missing', '不存在此晶圆', 404)
    return decode(row[0]), row[1]


def list_wafers(conn):
    return [{'id': name, 'revision': seq} for name, seq in conn.execute('SELECT id, revision FROM wafers ORDER BY id')]
