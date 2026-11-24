"""分区交付：待交付方案、正式交付件与整片交接清册。

分区在登记晶圆时冻结，不能后加或改写。每个交付区独立准备方案与正式交付，
整片签出在分区模式下停用；需要整片视图时只生成交接清册，它按物理格点标出
各正式件交付了哪一档，并随附每个正式件供离线核对，但不构成新的整片签出。
"""

from . import events
from .codec import decode, digest, encode
from .errors import DomainError
from .geometry import build_mask, order
from .grading import FINAL
from .partition import normalize, testable, zone_ids, zone_sites, zones_of
from .sorter import entries_as_rows, zone_entries
from .timeline import replay_timeline
from .validation import identifier, integer
from .wafers import load_wafer, save

PLAN_FORMAT = 'diegrade.zone-plan.1'
RELEASE_FORMAT = 'diegrade.zone-release.1'
LEDGER_FORMAT = 'diegrade.zone-ledger.1'


def _timeline(conn, spec):
    return replay_timeline(spec, events.read(conn, spec['id']))


def _zoned(spec):
    ids = zone_ids(spec)
    if not ids:
        raise DomainError('not_zoned', '该晶圆未启用分区交付模式', 409)
    return ids


def _zone(spec, value):
    _zoned(spec)
    zone_id = identifier(value)
    return zone_id, zone_sites(spec, zone_id)


def plan_summary(plan):
    body = plan['payload']
    return {'zone': body['zone'], 'seq': body['seq'], 'sha256': plan['sha256'],
            'revision': body['revision'], 'sites': body['sites']}


def release_summary(release):
    body = release['payload']
    return {'zone': body['zone'], 'sha256': release['sha256'], 'rows': len(body['rows'])}


def enable_partition(conn, spec, revision, data):
    """启用并冻结分区；分区不改变物理邻域与边缘，启用后不能改划。"""
    if not isinstance(data, dict) or set(data) not in ({'expected', 'zones'}, {'expected', 'partition'}):
        raise DomainError('fields', '请求字段应恰为：expected、zones')
    events.check_revision(data['expected'], revision)
    if zones_of(spec):
        raise DomainError('zone_locked', '该晶圆的分区已经冻结', 409)
    if conn.execute('SELECT 1 FROM releases WHERE wafer=? LIMIT 1', (spec['id'],)).fetchone():
        raise DomainError('zone_late', '本片已有整片分选版，不能改成分区交付', 409)
    physical, reserved = build_mask(spec['spans'], spec['reserved'])
    zones = normalize(data.get('zones', data.get('partition')), physical, reserved)
    if not zones:
        raise DomainError('zone_empty', '启用分区模式须给出至少一个分区')
    updated = {**spec, 'zones': zones}
    save(conn, updated)
    return {'wafer': spec['id'], 'revision': revision,
            'zones': [{'id': zone['id'], 'sites': len(zone['sites'])} for zone in zones]}


def prepare(conn, wafer_id, data):
    if not isinstance(data, dict) or 'expected' not in data or set(data) - {'expected', 'zone'}:
        raise DomainError('fields', '请求字段应恰为：expected、zone')
    spec, revision = load_wafer(conn, wafer_id)
    zone_id, sites = _zone(spec, data['zone'])
    events.check_revision(data['expected'], revision)
    entries = zone_entries(_timeline(conn, spec), sites)
    captured = digest({'zone': zone_id, 'entries': entries})
    for (stored,) in conn.execute('SELECT body FROM zone_plans WHERE wafer=? AND zone=? ORDER BY seq',
                                  (wafer_id, zone_id)):
        plan = decode(stored)
        if digest({'zone': zone_id, 'entries': plan['payload']['entries']}) == captured:
            return {'zone': zone_id, 'plan': plan, 'duplicate': True}
    seq = conn.execute('SELECT COALESCE(MAX(seq), 0) + 1 FROM zone_plans WHERE wafer=? AND zone=?',
                       (wafer_id, zone_id)).fetchone()[0]
    body = {'format': PLAN_FORMAT, 'wafer': wafer_id, 'zone': zone_id, 'seq': seq,
            'revision': revision, 'sites': [list(site) for site in order(sites)], 'entries': entries}
    plan = {'payload': body, 'sha256': digest(body)}
    conn.execute('INSERT INTO zone_plans VALUES (?, ?, ?, ?, ?)',
                 (wafer_id, zone_id, seq, plan['sha256'], encode(plan)))
    return {'zone': zone_id, 'plan': plan, 'duplicate': False}


def load_plan(conn, wafer_id, zone_id, seq):
    spec, _ = load_wafer(conn, wafer_id)
    zone_id, _ = _zone(spec, zone_id)
    seq = integer(seq, 1, 1000000, '方案序号')
    row = conn.execute('SELECT body FROM zone_plans WHERE wafer=? AND zone=? AND seq=?',
                       (wafer_id, zone_id, seq)).fetchone()
    if not row:
        raise DomainError('plan_missing', '待交付方案不存在', 404)
    plan = decode(row[0])
    verify_plan(spec, plan)
    return plan


def verify_plan(spec, plan):
    body = plan.get('payload') if isinstance(plan, dict) else None
    if not isinstance(body, dict) or body.get('format') != PLAN_FORMAT or digest(body) != plan.get('sha256'):
        raise DomainError('plan_digest', '待交付方案结构或摘要不一致', 409)
    if body.get('wafer') != spec['id']:
        raise DomainError('plan_digest', '待交付方案不属于本晶圆', 409)
    try:
        sites = zone_sites(spec, body['zone'])
        listed_sites = [tuple(site) for site in body['sites']]
        entries = body['entries']
        listed = [tuple(entry['site']) for entry in entries]
    except (KeyError, TypeError) as exc:
        raise DomainError('plan_digest', '待交付方案字段不完整', 409) from exc
    if set(listed_sites) != sites or len(listed_sites) != len(sites):
        raise DomainError('plan_digest', '待交付方案的分区格点与冻结分区不一致', 409)
    if len(listed) != len(sites) or set(listed) != sites or len(set(listed)) != len(listed):
        raise DomainError('plan_digest', '待交付方案未逐点覆盖本区格点', 409)
    if any(entry.get('bin') not in FINAL for entry in entries):
        raise DomainError('plan_digest', '待交付方案含非终档结果', 409)
    return plan


def _entry_matches(timeline, entry):
    site = tuple(entry['site'])
    active = timeline.active.get(site)
    return timeline.state(site) == entry['bin'] and active is not None \
        and active['batch'] == entry['batch'] and active['revision'] == entry['revision']


def review(conn, wafer_id, zone_id, seq):
    """核对视图：逐点说明冻结档位与来源是否仍是现场终判，不提供可执行清单。"""
    spec, revision = load_wafer(conn, wafer_id)
    zone_id, _ = _zone(spec, zone_id)
    plan = load_plan(conn, wafer_id, zone_id, seq)
    timeline = _timeline(conn, spec)
    entries = []
    for entry in plan['payload']['entries']:
        entries.append({'site': entry['site'], 'bin': entry['bin'], 'batch': entry['batch'],
                        'revision': entry['revision'], 'unchanged': _entry_matches(timeline, entry)})
    return {'zone': zone_id, 'seq': plan['payload']['seq'], 'plan_sha256': plan['sha256'],
            'frozen_at': plan['payload']['revision'], 'expected': revision,
            'entries': entries, 'stale': any(not entry['unchanged'] for entry in entries)}


def release(conn, wafer_id, data):
    if not isinstance(data, dict) or 'expected' not in data or 'zone' not in data \
            or set(data) - {'expected', 'zone', 'plan'}:
        raise DomainError('fields', '请求字段应恰为：expected、zone，可附 plan')
    spec, revision = load_wafer(conn, wafer_id)
    zone_id, _ = _zone(spec, data['zone'])
    events.check_revision(data['expected'], revision)
    if 'plan' in data:
        plan = load_plan(conn, wafer_id, zone_id, data['plan'])
    else:
        newest = conn.execute('SELECT MAX(seq) FROM zone_plans WHERE wafer=? AND zone=?',
                              (wafer_id, zone_id)).fetchone()[0]
        if newest is None:
            raise DomainError('plan_missing', '本区尚无待交付方案', 404)
        plan = load_plan(conn, wafer_id, zone_id, newest)
    existing = conn.execute('SELECT body FROM zone_releases WHERE wafer=? AND zone=?',
                            (wafer_id, zone_id)).fetchone()
    if existing:
        frozen = decode(existing[0])
        if frozen['payload']['plan']['sha256'] != plan['sha256']:
            raise DomainError('zone_released', '本区已正式交付，不能生成第二份可执行清单', 409)
        return {'zone': zone_id, 'release': frozen, 'duplicate': True}
    timeline = _timeline(conn, spec)
    for entry in plan['payload']['entries']:
        if not _entry_matches(timeline, entry):
            raise DomainError('plan_stale', f'格点 {tuple(entry["site"])} 的档位或有效量测已改变，请重新准备方案', 409)
    body = {'format': RELEASE_FORMAT, 'wafer': wafer_id, 'zone': zone_id,
            'rows': entries_as_rows(plan['payload']['entries']),
            'records': [{'zone': zone_id, **entry} for entry in plan['payload']['entries']],
            'plan': plan}
    frozen = {'payload': body, 'sha256': digest(body)}
    conn.execute('INSERT INTO zone_releases VALUES (?, ?, ?, ?)',
                 (wafer_id, zone_id, frozen['sha256'], encode(frozen)))
    return {'zone': zone_id, 'release': frozen, 'duplicate': False}


def verify_release(spec, release):
    body = release.get('payload') if isinstance(release, dict) else None
    if not isinstance(body, dict) or body.get('format') != RELEASE_FORMAT or digest(body) != release.get('sha256'):
        raise DomainError('zone_release_digest', '正式交付件结构或摘要不一致', 409)
    if body.get('wafer') != spec['id']:
        raise DomainError('zone_release_digest', '正式交付件不属于本晶圆', 409)
    try:
        sites = zone_sites(spec, body['zone'])
        records = body['records']
        listed = [tuple(record['site']) for record in records]
        rows = body['rows']
        plan = body['plan']
    except (KeyError, TypeError) as exc:
        raise DomainError('zone_release_digest', '正式交付件字段不完整', 409) from exc
    if len(listed) != len(sites) or set(listed) != sites:
        raise DomainError('zone_release_digest', '正式交付件未恰好覆盖本区格点', 409)
    if any(record.get('bin') not in FINAL for record in records):
        raise DomainError('zone_release_digest', '正式交付件含非终档结果', 409)
    verify_plan(spec, plan)
    if plan['payload']['zone'] != body['zone']:
        raise DomainError('zone_release_digest', '正式交付件与方案分区不一致', 409)
    try:
        expected = entries_as_rows(plan['payload']['entries'])
    except (KeyError, TypeError) as exc:
        raise DomainError('zone_release_digest', '正式交付件方案字段不完整', 409) from exc
    if expected != rows:
        raise DomainError('zone_release_digest', '正式交付件分选行与冻结方案不一致', 409)
    return release


def _load_release(conn, wafer_id, spec, zone_id):
    row = conn.execute('SELECT body FROM zone_releases WHERE wafer=? AND zone=?',
                       (wafer_id, zone_id)).fetchone()
    if not row:
        raise DomainError('zone_release_missing', '本区尚无正式交付件', 404)
    release = decode(row[0])
    verify_release(spec, release)
    return release


def delivered(conn, wafer_id, zone_id):
    """正式交付件可反复重取；不随现场复测改档而变化。"""
    spec, _ = load_wafer(conn, wafer_id)
    zone_id, _ = _zone(spec, zone_id)
    return _load_release(conn, wafer_id, spec, zone_id)


def ledger(conn, wafer_id):
    """整片交接清册：不再执行分选，只汇整已经冻结的正式件。"""
    spec, revision = load_wafer(conn, wafer_id)
    ids = _zoned(spec)
    pieces = []
    for zone_id in ids:
        try:
            pieces.append(_load_release(conn, wafer_id, spec, zone_id))
        except DomainError as exc:
            if exc.code != 'zone_release_missing':
                raise
            raise DomainError('zone_incomplete', f'分区 {zone_id} 尚未正式交付', 409) from exc
    cells = _collect(spec, pieces)
    body = {'format': LEDGER_FORMAT, 'wafer': wafer_id, 'revision': revision, 'spec': spec,
            'zones': [{'zone': piece['payload']['zone'], 'sha256': piece['sha256'], 'release': piece['payload']}
                      for piece in pieces],
            'cells': cells}
    return {'payload': body, 'sha256': digest(body)}


def _collect(spec, pieces):
    released = {}
    for piece in pieces:
        sha = piece['sha256']
        for record in piece['payload']['records']:
            site = tuple(record['site'])
            if site in released:
                raise DomainError('zone_overlap', f'多个正式件包含格点 {site}', 409)
            released[site] = {'site': record['site'], 'zone': record['zone'], 'bin': record['bin'],
                              'batch': record['batch'], 'revision': record['revision'], 'release': sha}
    expected = testable(spec)
    if set(released) != expected or len(released) != len(expected):
        raise DomainError('zone_coverage', '正式交付件未合计覆盖全部可测格点', 409)
    return [released[site] for site in order(expected)]


def verify_ledger(spec, value):
    """用可信晶圆分区复算整份清册；离线核对时由清册自带的晶圆正文充当 spec。"""
    body = value.get('payload') if isinstance(value, dict) else None
    if not isinstance(body, dict) or body.get('format') != LEDGER_FORMAT or digest(body) != value.get('sha256'):
        raise DomainError('ledger_digest', '交接清册结构或摘要不一致', 409)
    if body.get('wafer') != spec['id']:
        raise DomainError('ledger_digest', '交接清册不属于本晶圆', 409)
    ids = _zoned(spec)
    pieces = body.get('zones')
    if not isinstance(pieces, list) or len(pieces) != len(ids):
        raise DomainError('ledger_digest', '交接清册未按冻结分区列出全部正式件', 409)
    try:
        if [piece.get('zone') for piece in pieces] != ids:
            raise DomainError('ledger_digest', '交接清册未按冻结分区列出全部正式件', 409)
    except AttributeError as exc:
        raise DomainError('ledger_digest', '交接清册分区项结构不正确', 409) from exc
    claimed = {}
    for piece in pieces:
        release = {'payload': piece.get('release'), 'sha256': piece.get('sha256')}
        verify_release(spec, release)
        for record in piece['release']['records']:
            site = tuple(record['site'])
            if site in claimed:
                raise DomainError('zone_overlap', f'随附正式件重复包含格点 {site}', 409)
            claimed[site] = (record, piece['sha256'])
    expected = testable(spec)
    if set(claimed) != expected or len(claimed) != len(expected):
        raise DomainError('ledger_coverage', '随附正式件未合计覆盖全部可测格点', 409)
    cells = body.get('cells')
    if not isinstance(cells, list) or len(cells) != len(expected):
        raise DomainError('ledger_coverage', '交接清册未恰好覆盖全部可测格点', 409)
    seen = set()
    for cell in cells:
        try:
            site = tuple(cell['site'])
            record, sha = claimed[site]
            zone_cell = cell['zone']
            bin_cell = cell['bin']
            batch_cell = cell['batch']
            revision_cell = cell['revision']
            release_cell = cell['release']
        except (KeyError, TypeError) as exc:
            raise DomainError('ledger_cell', '交接清册格点与随附正式件不一致', 409) from exc
        if site in seen:
            raise DomainError('ledger_overlap', f'交接清册重复列出格点 {site}', 409)
        seen.add(site)
        if record['zone'] != zone_cell or record['bin'] != bin_cell or record['batch'] != batch_cell \
                or record['revision'] != revision_cell or sha != release_cell:
            raise DomainError('ledger_cell', f'交接清册格点 {site} 与随附正式件不一致', 409)
    if seen != expected:
        raise DomainError('ledger_coverage', '交接清册未恰好覆盖全部可测格点', 409)
    return {'zones': len(ids), 'cells': len(expected), 'sha256': value['sha256'], 'verified': True}


def verify_ledger_offline(value):
    """离线核对：清册自带冻结晶圆正文，逐区复算档位、来源与全片覆盖。"""
    body = value.get('payload') if isinstance(value, dict) else None
    if not isinstance(body, dict) or not isinstance(body.get('spec'), dict):
        raise DomainError('ledger_digest', '交接清册未附晶圆正文，不能离线核对', 409)
    return verify_ledger(body['spec'], value)


def ledger_rows(value):
    """交接清册的表格形式：逐格点标出交付区与正式件摘要，不是整片分选表。"""
    return [{'x': cell['site'][0], 'y': cell['site'][1], 'zone': cell['zone'], 'bin': cell['bin'],
             'batch': cell['batch'], 'release': cell['release']}
            for cell in value['payload']['cells']]
