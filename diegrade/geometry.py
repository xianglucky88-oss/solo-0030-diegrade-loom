from .errors import DomainError
from .validation import integer


def point(value, label='坐标'):
    if not isinstance(value, list) or len(value) != 2:
        raise DomainError('point', f'{label}须为两个整数')
    return tuple(integer(v, -2048, 2048, label) for v in value)


def build_mask(spans, reserved):
    if not isinstance(spans, list) or not 1 <= len(spans) <= 96 or not isinstance(reserved, list):
        raise DomainError('mask', '物理掩模须有 1–96 条行段及保留点数组')
    physical = set()
    for span in spans:
        if not isinstance(span, list) or len(span) != 3:
            raise DomainError('span', '每条行段须为 [y, x起, x止]')
        y, first, last = (integer(v, -48, 48, '掩模坐标') for v in span)
        if first > last:
            raise DomainError('span', '行段起点不得大于终点')
        segment = {(x, y) for x in range(first, last + 1)}
        if physical & segment:
            raise DomainError('mask_overlap', '同一物理点不能出现在多个行段')
        physical |= segment
    held = {point(v, '保留点') for v in reserved}
    if len(held) != len(reserved) or not held <= physical or not 1 <= len(physical) <= 4096 or physical == held:
        raise DomainError('mask_sites', '掩模必须含 1–4096 个物理点及至少一个待测点；保留点须唯一且在掩模内')
    return physical, held


def is_edge(site, physical):
    x, y = site
    return any(neighbor not in physical for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)))


def order(sites):
    return sorted(sites, key=lambda site: (site[1], site[0]))
