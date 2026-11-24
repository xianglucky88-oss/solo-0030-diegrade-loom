"""规则试评旁路。

试评固定一份已签出的原签件和一版备选规则，在不推进晶圆现场代次、
不改动原签件、也不生成生产分选指令的前提下，逐点复算历史证据并按需
开出带编号的试评补测单。探针回来件按补测单接收，判档结果只归属试评。
"""
from . import events
from .archives import load_release
from .batches import map_sites, prepare
from .codec import decode, encode
from .errors import DomainError
from .geometry import build_mask, is_edge, point
from .grading import FINAL, grade
from .rules import load
from .validation import fields, identifier, integer
from .wafers import load_wafer

ORDER_REASONS = {
    'history_broken': '历史记录在新规则下失去当时的接纳资格',
    'still_review': '历史证据仍适用，但该点在新规则下仍待复测',
    'missing': '该点没有可被新规则接纳的历史量测',
}


def snapshot(conn, wafer_id, data):
    """取原签件与备选规则，组成自包含的试评快照。"""
    fields(data, 'id', 'release', 'rule', 'rule_version')
    trial_id = identifier(data['id'])
    release_seq = integer(data['release'], 1, events.LIMIT, '签出代次')
    rule_name = identifier(data['rule'])
    rule_version = integer(data['rule_version'], 1, 9999, '备选规则版本')
    spec, revision = load_wafer(conn, wafer_id)
    if release_seq > revision:
        raise DomainError('release_missing', '该代次尚无签出件', 404)
    release = load_release(conn, wafer_id, release_seq)
    candidate = load(conn, rule_name, rule_version)
    if candidate['name'] == spec['rule']['name'] and candidate['version'] == spec['rule']['version']:
        raise DomainError('rule_same', '备选规则须与原签件规则不同')
    body = release['payload']
    return trial_id, {'wafer': spec, 'release_revision': release_seq,
                      'original_rule': spec['rule'], 'candidate_rule': candidate,
                      'events': body['events']}


def create(conn, wafer_id, data):
    trial_id, snap = snapshot(conn, wafer_id, data)
    row = conn.execute('SELECT snapshot FROM trials WHERE wafer=? AND trial=?',
                       (wafer_id, trial_id)).fetchone()
    if row:
        if decode(row[0]) != snap:
            raise DomainError('trial_conflict', '该试评编号已固定另一签件或备选规则', 409)
        return detail(conn, wafer_id, trial_id)
    assessment = evaluate(snap)
    conn.execute('INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?)',
                 (wafer_id, trial_id, snap['release_revision'], snap['candidate_rule']['name'],
                  snap['candidate_rule']['version'], encode(snap)))
    for order in assessment['orders']:
        conn.execute(
            'INSERT INTO trial_orders(wafer, trial, seq, site, reason_code, reason, first_ref, open) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
            (wafer_id, trial_id, order['seq'], encode(order['site']), order['reason_code'],
             order['reason'], order['first_ref']))
    return detail(conn, wafer_id, trial_id)


def load_trial(conn, wafer_id, trial_id):
    row = conn.execute('SELECT snapshot FROM trials WHERE wafer=? AND trial=?',
                       (wafer_id, identifier(trial_id))).fetchone()
    if not row:
        raise DomainError('trial_missing', '不存在此试评', 404)
    return decode(row[0])


def order_list(conn, wafer_id, trial_id):
    rows = conn.execute(
        'SELECT seq, site, reason_code, reason, first_ref, open FROM trial_orders '
        'WHERE wafer=? AND trial=? ORDER BY seq', (wafer_id, trial_id)).fetchall()
    return [{'id': f'O{seq:02d}', 'seq': seq, 'site': decode(site), 'reason_code': code,
             'reason': text, 'first_ref': ref, 'status': 'open' if open_ else 'closed'}
            for seq, site, code, text, ref, open_ in rows]


def batch_list(conn, wafer_id, trial_id):
    rows = conn.execute('SELECT batch_id, body FROM trial_batches WHERE wafer=? AND trial=? '
                        'ORDER BY rowid', (wafer_id, trial_id)).fetchall()
    return [decode(body) for _, body in rows]


def list_trials(conn, wafer_id):
    return [{'id': trial, 'release': release_seq, 'rule': name, 'rule_version': version}
            for trial, release_seq, name, version in conn.execute(
                'SELECT trial, release_revision, rule_name, rule_version FROM trials '
                'WHERE wafer=? ORDER BY trial', (wafer_id,))]


def evaluate(snap):
    """以备选规则重放原签件的完整历史，逐点给出试评结论。"""
    rule = snap['candidate_rule']
    spec = snap['wafer']
    physical, reserved = _mask(spec)
    chains = {site: [] for site in physical}
    for event in snap['events']:
        body = event['body']
        if event['kind'] == 'hold':
            chains[tuple(body['site'])].append(('hold', event['revision'], body))
            continue
        # 与生产现场共用同一套设备位姿归一和物理格点校验。
        for site, record in map_sites(body, physical, reserved):
            chains[site].append(('measurement', event['revision'], body, record))

    cells, orders = [], []
    for site in sorted(physical, key=lambda item: (item[1], item[0])):
        cell = assess_site(site, chains[site], rule, physical, reserved)
        cells.append(cell)
        if cell['order']:
            orders.append(cell['order'])
    for seq, order in enumerate(orders, 1):
        order['seq'] = seq
        order['id'] = f'O{seq:02d}'
    return {'cells': cells, 'orders': orders}


def _mask(spec):
    return build_mask(spec['spans'], spec['reserved'])


def assess_site(site, chain, rule, physical, reserved):
    edge = is_edge(site, physical)
    if site in reserved:
        return {'site': list(site), 'edge': edge, 'state': 'reserved', 'basis': '保留点不投测',
                'evidence': [], 'skipped': [], 'order': None}
    evidence, skipped = [], []
    active = None          # 最近一条被试评规则接纳的量测判档
    reviewing = False      # 该点当前是否处于新规则认可的待复测状态
    reopened = False       # 最近一次合法复核是否真正重开了复测
    broken = None          # 最早失去接纳资格的历史记录
    for item in chain:
        if broken:
            kind = item[0]
            ref = item[2].get('id') if kind == 'hold' else item[2]['id']
            skipped.append({'ref': ref, 'kind': kind, 'why': '接纳链已中断，后续旧记录不再采纳'})
            continue
        if item[0] == 'hold':
            _, revision, body = item
            # 复核合法性以当时证据为准：能进入历史的复核，其旧规则下当时已终判。
            # 新规则下若该量测使点处于待复测，复核才真正重开；否则复核留痕但不重开。
            if active is None:
                broken = {'ref': body['id'], 'kind': 'hold', 'revision': revision,
                          'why': '复核提交时该点没有有效量测，不能据此重开复测'}
                continue
            hold = {'event': 'hold', 'revision': revision, 'batch': active['batch'],
                    'why': body['reason'], 'accepted': True,
                    'reopened': active['state'] == 'review'}
            evidence.append(hold)
            reopened = hold['reopened']
            reviewing = reviewing or reopened
            continue
        _, revision, batch, record = item
        repeated = batch['kind'] == 'repeat'
        decision = grade(rule, record, edge, repeated)
        entry = {'event': 'measurement', 'revision': revision, 'batch': batch['id'],
                 'kind': batch['kind'], 'frame': batch['frame'], 'record': record, **decision}
        if repeated:
            # 沿用旧复测须有证据：原父量测仍是该点当前有效量测，且新规则下确实待复测。
            if active is None or record['parent'] != active['batch'] or not (reviewing or reopened):
                state = '未在待复测状态提交' if not (reviewing or reopened) else '父量测不是当前有效量测'
                entry['accepted'] = False
                entry['reject_why'] = f'复测提交时{state}，新规则不承认其待复测父状态'
                broken = {'ref': batch['id'], 'kind': 'repeat', 'revision': revision,
                          'why': entry['reject_why']}
                skipped.append(entry)
                continue
        entry['accepted'] = True
        evidence.append(entry)
        active = entry
        reviewing = entry['state'] == 'review'
        reopened = False

    order = None
    if broken:
        state = 'review'
        order = broken_order(site, broken, 'history_broken')
        basis = broken['why']
    elif active is None:
        state = 'review'
        first = chain[0][2]['id'] if chain else ''
        order = {'site': list(site), 'reason_code': 'missing',
                 'reason': ORDER_REASONS['missing'], 'first_ref': first}
        basis = ORDER_REASONS['missing']
    elif reviewing:
        state = 'review'
        order = {'site': list(site), 'reason_code': 'still_review',
                 'reason': ORDER_REASONS['still_review'] + '：' + active['why'],
                 'first_ref': active['batch']}
        basis = active['why']
    else:
        state = active['state']
        basis = active['why']
    return {'site': list(site), 'edge': edge, 'state': state, 'basis': basis,
            'evidence': evidence, 'skipped': skipped, 'order': order}


def broken_order(site, broken, code):
    label = '复核' if broken['kind'] == 'hold' else '复测'
    return {'site': list(site), 'reason_code': code,
            'reason': f"{ORDER_REASONS[code]}：最早失去资格的是{label} {broken['ref']}（{broken['why']}）",
            'first_ref': broken['ref']}


def detail(conn, wafer_id, trial_id):
    snap = load_trial(conn, wafer_id, trial_id)
    assessment = evaluate(snap)
    orders = {tuple(order['site']): order for order in order_list(conn, wafer_id, trial_id)}
    cells = []
    for cell in assessment['cells']:
        site = tuple(cell['site'])
        view = {key: cell[key] for key in ('site', 'edge', 'state', 'basis', 'evidence', 'skipped')}
        view['order'] = orders.get(site)
        cells.append(view)
    index = {tuple(cell['site']): cell for cell in cells}
    # 按入账顺序叠加本次试评的探针回来件（含开路后等待、再补测的多次记录）。
    for stored in batch_list(conn, wafer_id, trial_id):
        for entry in stored['entries']:
            cell = index[tuple(entry['site'])]
            cell['evidence'].append(entry)
            cell['state'] = entry['state']
            cell['basis'] = '本次试评补测：' + entry['why']
    counts = {}
    for cell in cells:
        counts[cell['state']] = counts.get(cell['state'], 0) + 1
    return {'id': trial_id, 'wafer': snap['wafer']['id'], 'release': snap['release_revision'],
            'original_rule': snap['original_rule'], 'candidate_rule': snap['candidate_rule'],
            'cells': cells, 'counts': counts, 'orders': order_list(conn, wafer_id, trial_id),
            'batches': batch_list(conn, wafer_id, trial_id),
            'ready': not any(order['status'] == 'open' for order in orders.values())}


def site_detail(conn, wafer_id, trial_id, site):
    target = point(list(site), '格点')
    view = detail(conn, wafer_id, trial_id)
    for cell in view['cells']:
        if tuple(cell['site']) == target:
            return cell
    raise DomainError('site_missing', '格点不属于本片晶圆', 404)


def submit(conn, wafer_id, trial_id, data):
    """接收探针工位按试评补测单送回的量测，整批校验、原子入账。"""
    fields(data, 'batch')
    batch = prepare(data['batch'])
    if batch['kind'] != 'repeat':
        raise DomainError('trial_kind', '试评回来件必须是引用补测单的复测')
    snap = load_trial(conn, wafer_id, trial_id)
    physical, reserved = _mask(snap['wafer'])
    orders = order_list(conn, wafer_id, trial_id)
    open_orders = {tuple(order['site']): order for order in orders if order['status'] == 'open'}
    mapped = map_sites(batch, physical, reserved)

    prior = conn.execute('SELECT body, frame FROM trial_batches WHERE wafer=? AND trial=? AND batch_id=?',
                         (wafer_id, trial_id, batch['id'])).fetchone()
    if prior:
        stored, stored_frame = prior
        previous = decode(stored)
        if previous['rows'] != batch['rows'] or stored_frame != encode(batch['frame']):
            raise DomainError('batch_conflict', '批次编号已用于另一份试评回来件', 409)
        return {'duplicate': True, 'batch': batch['id'], 'changes': []}

    # 整批校验：任一行对不上本次试评尚未关闭的补测单或归一后的物理格点，整批不生效。
    for site, record in mapped:
        order = open_orders.get(site)
        if order is None:
            raise DomainError('trial_order', f'格点 {list(site)} 没有本次试评待关闭的补测单', 422)
        if record['parent'] != order['id']:
            raise DomainError('trial_order_ref',
                              f"格点 {list(site)} 引用的 {record['parent']} 不是其补测单 {order['id']}", 422)

    rule, spec = snap['candidate_rule'], snap['wafer']
    changes, entries = [], []
    for site, record in mapped:
        edge = is_edge(site, physical)
        decision = grade(rule, record, edge, repeated=True)
        entry = {'event': 'trial_measurement', 'site': list(site), 'batch': batch['id'],
                 'frame': batch['frame'], 'record': record, **decision}
        entries.append(entry)
        order = open_orders[site]
        if decision['state'] in FINAL:
            order['status'] = 'closed'
            conn.execute('UPDATE trial_orders SET open=0 WHERE wafer=? AND trial=? AND seq=?',
                         (wafer_id, trial_id, order['seq']))
        else:
            # 开路等仍待复测：补测单保留原因并继续等待，不关闭。
            conn.execute('UPDATE trial_orders SET reason=? WHERE wafer=? AND trial=? AND seq=?',
                         (order['reason'] + '；回来件仍待复测：' + decision['why'],
                          wafer_id, trial_id, order['seq']))
        changes.append({'site': list(site), 'state': decision['state'], 'why': decision['why']})
    conn.execute('INSERT INTO trial_batches VALUES (?, ?, ?, ?, ?)',
                 (wafer_id, trial_id, batch['id'], encode(batch['frame']),
                  encode({'id': batch['id'], 'frame': batch['frame'], 'rows': batch['rows'],
                          'entries': entries, 'changes': changes})))
    return {'duplicate': False, 'batch': batch['id'], 'changes': changes}
