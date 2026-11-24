import csv
import io

from .errors import DomainError
from .grading import FINAL


def assignments(timeline):
    grid = [cell for cell in timeline.grid() if cell['state'] != 'reserved']
    pending = sum(cell['state'] not in FINAL for cell in grid)
    if pending:
        raise DomainError('not_ready', f'仍有 {pending} 颗未测或待复测，无法交付', 409)
    return [assignment(cell) for cell in grid]


def assignment(cell):
    row = {'x': cell['site'][0], 'y': cell['site'][1], 'bin': cell['state']}
    if 'conditions' in cell:
        row['batch'] = {name: part['batch'] for name, part in cell['conditions'].items()}
        row['margin'] = {name: part['margin_mv'] for name, part in cell['conditions'].items()}
    else:
        row['batch'] = cell['batch']
    return row


def as_csv(rows):
    buffer = io.StringIO(newline='')
    names = list(rows[0]['batch']) if rows and isinstance(rows[0]['batch'], dict) else None
    header = ['x', 'y', 'bin', 'batch'] if names is None else (
        ['x', 'y', 'bin'] + [f'batch_{name}' for name in names] + [f'margin_{name}' for name in names])
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        if names is not None:
            row = {'x': row['x'], 'y': row['y'], 'bin': row['bin'],
                   **{f'batch_{name}': row['batch'][name] for name in names},
                   **{f'margin_{name}': row['margin'][name] for name in names}}
        writer.writerow(row)
    return buffer.getvalue()
