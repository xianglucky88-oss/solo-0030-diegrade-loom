from .errors import DomainError
from .frames import frame, normalize
from .geometry import order
from .probe_csv import parse
from .validation import fields, identifier


def prepare(data):
    fields(data, 'id', 'kind', 'frame', 'csv')
    identifier(data['id'])
    if data['kind'] not in ('screen', 'repeat'):
        raise DomainError('kind', '批次须为首测或复测')
    return {'id': data['id'], 'kind': data['kind'], 'frame': frame(data['frame']),
            'rows': sorted(parse(data['csv'], data['kind']), key=lambda row: (row['y'], row['x']))}


def map_sites(batch, physical, reserved):
    mapped = {}
    for record in batch['rows']:
        site = normalize(batch['frame'], record['x'], record['y'])
        if site in mapped or site in reserved or site not in physical:
            raise DomainError('mapped_site', f'格点 {site} 重叠、被保留或不属于晶圆')
        mapped[site] = record
    return [(site, mapped[site]) for site in order(mapped)]
