from .codec import decode, encode
from .errors import DomainError
from .validation import fields, identifier, integer


def register(conn, data):
    spec = validate(data)
    key = (spec['name'], spec['version'])
    row = conn.execute('SELECT body FROM rules WHERE name=? AND version=?', key).fetchone()
    if row and decode(row[0]) != spec:
        raise DomainError('rule_conflict', '该规则版本已经冻结', 409)
    if not row:
        conn.execute('INSERT INTO rules VALUES (?, ?, ?)', (*key, encode(spec)))
    return spec


def validate(data):
    fields(data, 'name', 'version', 'leak_limit', 'margin_min', 'grade_a', 'edge_add', 'guard')
    identifier(data['name'])
    integer(data['version'], 1, 9999, '规则版本')
    integer(data['leak_limit'], 0, 1000000, '漏电上限')
    minimum = integer(data['margin_min'], 0, 10000, '裕量下限')
    integer(data['grade_a'], minimum + 1, 10000, '甲档下限')
    integer(data['edge_add'], 0, 10000 - data['grade_a'], '边缘增量')
    integer(data['guard'], 0, 10000, '复测带宽')
    return data


def load(conn, name, version):
    row = conn.execute('SELECT body FROM rules WHERE name=? AND version=?', (name, version)).fetchone()
    if not row:
        raise DomainError('rule_missing', '没有所选规则版本', 404)
    return decode(row[0])


def catalog(conn):
    return [decode(row[0]) for row in conn.execute('SELECT body FROM rules ORDER BY name, version DESC')]
