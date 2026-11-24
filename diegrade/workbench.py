from collections import Counter

from .intake import current
from .partition import zone_map, zones_of


def summary(conn, wafer_id):
    timeline, revision = current(conn, wafer_id)
    cells = timeline.grid()
    counts = Counter(cell['state'] for cell in cells)
    releases = [rev for (rev,) in conn.execute('SELECT revision FROM releases WHERE wafer=? ORDER BY revision DESC', (wafer_id,))]
    zones = zones_of(timeline.spec)
    result = {'wafer': timeline.spec, 'revision': revision, 'cells': cells, 'counts': counts,
              'ready': not (counts['missing'] or counts['review']), 'releases': releases, 'zoned': bool(zones)}
    if zones:
        result['zone'] = zone_state(conn, wafer_id, timeline, zones)
    return result


def zone_state(conn, wafer_id, timeline, zones):
    plans = {}
    for zone_id, seq in conn.execute('SELECT zone, MAX(seq) FROM zone_plans WHERE wafer=? GROUP BY zone', (wafer_id,)):
        plans[zone_id] = seq
    released = {zone_id: sha for zone_id, sha in conn.execute(
        'SELECT zone, sha256 FROM zone_releases WHERE wafer=?', (wafer_id,))}
    listed = []
    for zone in zones:
        members = [tuple(site) for site in zone['sites']]
        counts = Counter(timeline.state(site) for site in members)
        finalized = sum(counts[state] for state in ('a', 'b', 'reject'))
        listed.append({'id': zone['id'], 'sites': zone['sites'], 'counts': dict(counts),
                       'finalized': finalized, 'ready': finalized == len(members),
                       'plan': plans.get(zone['id']), 'release': released.get(zone['id'])})
    return {'count': len(listed), 'testable': sum(len(zone['sites']) for zone in listed),
            'zones': listed, 'all_ready': all(zone['ready'] for zone in listed),
            'delivered': all(zone['id'] in released for zone in listed)}


def trace(conn, wafer_id, site):
    timeline, revision = current(conn, wafer_id)
    result = {**timeline.cell(site), 'revision': revision, 'chain': timeline.history.get(site, [])}
    zone_id = zone_map(timeline.spec).get(tuple(site))
    if zone_id:
        result['zone'] = zone_id
    return result
