from collections import Counter

from . import cycles as cycle_model
from .intake import current


def summary(conn, wafer_id):
    timeline, revision = current(conn, wafer_id)
    cells = timeline.grid()
    counts = Counter(cell['state'] for cell in cells)
    releases = [rev for (rev,) in conn.execute('SELECT revision FROM releases WHERE wafer=? ORDER BY revision DESC', (wafer_id,))]
    return {'wafer': timeline.spec, 'revision': revision, 'cells': cells, 'counts': counts,
            'ready': not (counts['missing'] or counts['review']), 'releases': releases,
            'cycles': [cycle_model.view(state) for state in timeline.cycle_log]}


def trace(conn, wafer_id, site):
    timeline, revision = current(conn, wafer_id)
    return {**timeline.cell(site), 'revision': revision, 'chain': timeline.history.get(site, [])}


def cycles(conn, wafer_id):
    timeline, revision = current(conn, wafer_id)
    return {'revision': revision, 'cycles': [cycle_model.view(state) for state in timeline.cycle_log]}


def cycle(conn, wafer_id, cycle_id):
    timeline, revision = current(conn, wafer_id)
    return {**cycle_model.view(timeline.cycle(cycle_id)), 'revision': revision}
