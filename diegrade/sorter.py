import csv
import io

from .errors import DomainError
from .grading import FINAL


def assignments(timeline):
    grid = [cell for cell in timeline.grid() if cell['state'] != 'reserved']
    pending = sum(cell['state'] not in FINAL for cell in grid)
    if pending:
        raise DomainError('not_ready', f'仍有 {pending} 颗未测或待复测，无法交付', 409)
    return [{'x': cell['site'][0], 'y': cell['site'][1], 'bin': cell['state'], 'batch': cell['batch']} for cell in grid]


def as_csv(rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=['x', 'y', 'bin', 'batch'], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
