import csv
import io

from .errors import DomainError
from .geometry import order
from .grading import FINAL


def assignments(timeline):
    grid = [cell for cell in timeline.grid() if cell['state'] != 'reserved']
    pending = sum(cell['state'] not in FINAL for cell in grid)
    if pending:
        raise DomainError('not_ready', f'仍有 {pending} 颗未测或待复测，无法交付', 409)
    return [{'x': cell['site'][0], 'y': cell['site'][1], 'bin': cell['state'], 'batch': cell['batch']} for cell in grid]


def zone_cells(timeline, sites):
    cells = [timeline.cell(site) for site in order(sites)]
    pending = sum(cell['state'] not in FINAL for cell in cells)
    if pending:
        raise DomainError('zone_not_ready', f'本区仍有 {pending} 颗未测或待复测，无法准备交付方案', 409)
    return cells


def zone_rows(timeline, sites):
    return [{'x': cell['site'][0], 'y': cell['site'][1], 'bin': cell['state'], 'batch': cell['batch']}
            for cell in zone_cells(timeline, sites)]


def zone_entries(timeline, sites):
    """逐点冻结档位、有效量测来源及其现场代次。"""
    entries = []
    for cell in zone_cells(timeline, sites):
        site = tuple(cell['site'])
        active = timeline.active[site]
        entries.append({'site': cell['site'], 'bin': cell['state'], 'batch': cell['batch'],
                        'revision': active['revision']})
    return entries


def entries_as_rows(entries):
    return [{'x': entry['site'][0], 'y': entry['site'][1], 'bin': entry['bin'], 'batch': entry['batch']}
            for entry in entries]


def as_csv(rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=['x', 'y', 'bin', 'batch'], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
