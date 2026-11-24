from .errors import DomainError
from .frames import frame, normalize
from .geometry import order
from .probe_csv import parse
from .validation import identifier


def prepare(data):
    if not isinstance(data, dict):
        raise DomainError('batch', '批次须为对象')
    allowed = {'id', 'kind', 'frame', 'csv', 'cycle', 'cycle_id'}
    if not {'id', 'kind', 'csv'} <= set(data) or set(data) - allowed:
        raise DomainError('fields', '批次字段须为：id、kind、frame、csv，可另有 cycle')
    identifier(data['id'])
    if data['kind'] not in ('screen', 'repeat'):
        raise DomainError('kind', '批次须为首测或复测')
    cycle = data.get('cycle', data.get('cycle_id'))
    if data.get('cycle') is not None and data.get('cycle_id') is not None and data['cycle'] != data['cycle_id']:
        raise DomainError('cycle', '批次的周期字段互相冲突')
    if cycle is not None:
        identifier(cycle)
    mapping = data.get('frame')
    if mapping is None:
        if cycle is None:
            raise DomainError('fields', '未引用装夹周期的批次须自带坐标系')
    else:
        mapping = frame(mapping)
    return {'id': data['id'], 'kind': data['kind'], 'frame': mapping, 'cycle': cycle,
            'rows': sorted(parse(data['csv'], data['kind']), key=lambda row: (row['y'], row['x']))}


def map_sites(batch, physical, reserved, mapping=None):
    mapping = batch['frame'] if mapping is None else mapping
    if mapping is None:
        raise DomainError('frame', '批次没有可用坐标系')
    mapped = {}
    for record in batch['rows']:
        site = normalize(mapping, record['x'], record['y'])
        if site in mapped or site in reserved or site not in physical:
            raise DomainError('mapped_site', f'格点 {site} 重叠、被保留或不属于晶圆')
        mapped[site] = record
    return [(site, mapped[site]) for site in order(mapped)]
