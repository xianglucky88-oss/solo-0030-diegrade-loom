from .errors import DomainError
from .geometry import point
from .validation import fields, integer


def frame(value):
    fields(value, 'offset', 'turn', 'mirror')
    point(value['offset'], '平移')
    integer(value['turn'], 0, 3, '逆时针四分之一圈数')
    if type(value['mirror']) is not bool:
        raise DomainError('frame', '镜像值须为布尔值')
    return value


def orient(mapping, x, y):
    """按镜像、再逆时针旋转，得到未平移的设备坐标。"""
    if mapping['mirror']:
        x = -x
    for _ in range(mapping['turn']):
        x, y = -y, x
    return x, y


def normalize(mapping, x, y):
    x, y = orient(mapping, x, y)
    ox, oy = mapping['offset']
    return x + ox, y + oy


def same(first, second):
    return (first['turn'] == second['turn'] and first['mirror'] == second['mirror']
            and tuple(first['offset']) == tuple(second['offset']))

