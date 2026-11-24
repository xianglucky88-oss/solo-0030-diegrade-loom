"""固定分区：把可测格点划成互不重叠、合计覆盖全部可测点的若干交付区。"""

from .errors import DomainError
from .geometry import build_mask, order, point
from .validation import identifier

MAX_ZONES = 256
SITE_KEYS = ('sites', 'points', 'cells')
SPAN_KEYS = ('spans', 'ranges')


def testable(spec):
    physical, reserved = build_mask(spec['spans'], spec['reserved'])
    return physical - reserved


def zones_of(spec):
    return spec.get('zones') or []


def zone_ids(spec):
    return [zone['id'] for zone in zones_of(spec)]


def zone_map(spec):
    return {tuple(site): zone['id'] for zone in zones_of(spec) for site in zone['sites']}


def zone_sites(spec, zone_id):
    for zone in zones_of(spec):
        if zone['id'] == zone_id:
            return {tuple(site) for site in zone['sites']}
    raise DomainError('zone_missing', '没有这个交付区', 404)


def _site(value):
    if isinstance(value, dict):
        value = [value.get('x'), value.get('y')]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DomainError('zone_site', '分区格点须为 [x, y] 或带 x、y 的对象')
    return point(list(value))


def _spans_to_sites(spans):
    if not isinstance(spans, list):
        raise DomainError('zone_spans', '分区行段须为数组')
    result = set()
    for span in spans:
        if isinstance(span, dict):
            span = [span.get('y'), span.get('x0', span.get('first')), span.get('x1', span.get('last'))]
        if not isinstance(span, (list, tuple)) or len(span) != 3:
            raise DomainError('zone_spans', '分区行段须为 [y, x起, x止]')
        y, first, last = span
        if type(y) is not int or type(first) is not int or type(last) is not int or first > last:
            raise DomainError('zone_spans', '分区行段坐标须为整数且起点不大于终点')
        result |= {(x, y) for x in range(first, last + 1)}
    return result


def _definition_sites(value):
    if isinstance(value, dict):
        for key in SITE_KEYS:
            if key in value:
                sites = value[key]
                if not isinstance(sites, list):
                    raise DomainError('zone_site', '分区格点须为数组')
                return {_site(item) for item in sites}
        for key in SPAN_KEYS:
            if key in value:
                return _spans_to_sites(value[key])
        raise DomainError('zone_definition', '分区须给出 sites 或 spans')
    if not isinstance(value, list):
        raise DomainError('zone_definition', '分区内容须为格点数组')
    return {_site(item) for item in value}


def _entries(raw):
    if isinstance(raw, dict):
        if any(key in raw for key in SITE_KEYS + SPAN_KEYS):
            raise DomainError('zones', '分区须为编号到格点的映射，或带 id 的分区列表')
        return list(raw.items())
    if isinstance(raw, list):
        result = []
        for item in raw:
            if not isinstance(item, dict) or 'id' not in item:
                raise DomainError('zones', '分区列表项须为带 id、sites 或 spans 的对象')
            result.append((item['id'], item))
        return result
    raise DomainError('zones', '分区须为编号到格点的映射或分区列表')


def normalize(raw, physical, reserved):
    """返回按编号排序的规范分区；空输入表示未启用分区。"""
    if raw is None or raw == [] or raw == {}:
        return []
    seen_ids, covered, zones = set(), set(), []
    for zone_id, definition in _entries(raw):
        zone_id = identifier(zone_id)
        if zone_id in seen_ids:
            raise DomainError('zone_duplicate', '分区编号不能重复')
        seen_ids.add(zone_id)
        sites = _definition_sites(definition)
        if not sites:
            raise DomainError('zone_empty', '每个分区至少包含一个可测格点')
        for site in sites:
            if site not in physical:
                raise DomainError('zone_site', f'分区格点 {site} 不属于晶圆物理掩模')
            if site in reserved:
                raise DomainError('zone_reserved', f'保留点 {site} 不能进入分选分区')
            if site in covered:
                raise DomainError('zone_overlap', f'格点 {site} 出现在多个分区')
            covered.add(site)
        zones.append({'id': zone_id, 'sites': [list(site) for site in order(sites)]})
    if not 1 <= len(zones) <= MAX_ZONES:
        raise DomainError('zone_count', f'分区数量须为 1–{MAX_ZONES}')
    expected = physical - reserved
    missing = expected - covered
    if missing:
        sample = '、'.join(str(site) for site in order(missing)[:4])
        raise DomainError('zone_coverage', f'分区未覆盖全部可测格点，例如 {sample}')
    return sorted(zones, key=lambda zone: zone['id'])
