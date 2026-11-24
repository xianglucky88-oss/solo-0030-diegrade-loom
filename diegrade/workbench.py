from collections import Counter

from .intake import current


def consignment_list(timeline):
    return [{'id': ref, 'revision': item['revision'],
             'lines': [{'line': number, **fields} for number, fields in item['lines'].items()]}
            for ref, item in timeline.consignments.items()]


def summary(conn, wafer_id):
    timeline, revision = current(conn, wafer_id)
    cells = timeline.grid()
    counts = Counter(cell['state'] for cell in cells)
    releases = [rev for (rev,) in conn.execute('SELECT revision FROM releases WHERE wafer=? ORDER BY revision DESC', (wafer_id,))]
    return {'wafer': timeline.spec, 'revision': revision, 'cells': cells, 'counts': counts,
            'ready': not (counts['missing'] or counts['review']), 'releases': releases,
            'consignments': consignment_list(timeline)}


def trace(conn, wafer_id, site):
    timeline, revision = current(conn, wafer_id)
    return {**timeline.cell(site), 'revision': revision, 'chain': timeline.history.get(site, [])}
