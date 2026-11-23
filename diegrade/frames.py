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


def normalize(mapping, x, y):
    if mapping['mirror']:
        x = -x
    for _ in range(mapping['turn']):
        x, y = -y, x
    ox, oy = mapping['offset']
    return x + ox, y + oy
