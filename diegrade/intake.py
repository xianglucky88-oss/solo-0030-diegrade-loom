from . import cycles, events
from .batches import prepare
from .errors import DomainError
from .geometry import build_mask, point
from .timeline import replay_timeline
from .validation import fields, identifier, reason
from .wafers import load_wafer

MESSAGES = {'open': '尚未收到标记观测', 'undetermined': '可行位姿尚未确定',
            'inconsistent': '标记观测不一致', 'confirmed': '位姿已确认'}


def current(conn, wafer_id):
    spec, revision = load_wafer(conn, wafer_id)
    return replay_timeline(spec, events.read(conn, wafer_id)), revision


def intake(conn, wafer_id, data, write):
    fields(data, 'expected', 'batch')
    batch = prepare(data['batch'])
    spec, revision = load_wafer(conn, wafer_id)
    duplicate = events.find(conn, wafer_id, 'batch', batch['id'])
    events.check_revision(data['expected'], revision)
    if duplicate is not None:
        if isinstance(duplicate, dict) and 'cycle' not in duplicate:
            duplicate = {**duplicate, 'cycle': None}
        if duplicate != batch:
            raise DomainError('batch_conflict', '批次编号已用于另一份证据', 409)
        return {'revision': revision, 'duplicate': True, 'changes': []}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    changes = timeline.apply_batch(batch, revision + 1)
    if write:
        events.append(conn, wafer_id, revision + 1, 'batch', batch['id'], batch)
    return {'revision': revision + int(write), 'duplicate': False,
            'changes': [{'site': list(site), 'state': result['state']} for site, result in changes]}


def _cycle_payload(data):
    """周期正文可以包在 cycle 下，也可平铺；所属晶圆由 URL 给出。"""
    if 'cycle' in data:
        body = data['cycle']
    else:
        body = {key: value for key, value in data.items() if key not in ('expected', 'wafer')}
    if not isinstance(body, dict):
        raise DomainError('fields', '装夹周期须为对象')
    return body


def register_cycle(conn, wafer_id, data):
    if not isinstance(data, dict) or 'expected' not in data:
        raise DomainError('fields', '登记周期须含 expected')
    if 'wafer' in data and data['wafer'] != wafer_id:
        raise DomainError('cycle_wafer', '装夹周期所属晶圆与登记不符', 409)
    body = cycles.prepare(_cycle_payload(data), build_mask(*_mask(conn, wafer_id))[0])
    if body.get('wafer') not in (None, wafer_id):
        raise DomainError('cycle_wafer', '装夹周期所属晶圆与登记不符', 409)
    body['wafer'] = wafer_id
    return _register(conn, wafer_id, data['expected'], body)


def _mask(conn, wafer_id):
    spec, _ = load_wafer(conn, wafer_id)
    return spec['spans'], spec['reserved']


def _register(conn, wafer_id, expected, body):
    spec, revision = load_wafer(conn, wafer_id)
    events.check_revision(expected, revision)
    duplicate = events.find(conn, wafer_id, 'cycle', body['id'])
    if duplicate is not None:
        if duplicate != body:
            raise DomainError('cycle_conflict', '周期编号已用于另一份配置', 409)
        timeline = replay_timeline(spec, events.read(conn, wafer_id))
        return {'revision': revision, 'duplicate': True,
                'state': _view(timeline.cycle(body['id']))}
    if revision == events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    timeline.apply_cycle(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'cycle', body['id'], body)
    return {'revision': revision + 1, 'duplicate': False,
            'state': _view(cycles.state(body['id'], body, revision + 1))}


def _view(state):
    view = cycles.view(state)
    view['message'] = MESSAGES[view['state']]
    return view


def _observations(data):
    if not isinstance(data, dict):
        raise DomainError('fields', '标记观测须为对象')
    for key in ('observations', 'marks'):
        if key in data:
            source = data[key]
            if not isinstance(source, list) or not 1 <= len(source) <= cycles.MAX_MARKERS:
                raise DomainError('cycle_mark', '标记观测须为 1–64 条数组')
            return [cycles.observation(value) for value in source]
    return [cycles.observation(data)]


def observe(conn, wafer_id, data, cycle_id=None):
    if not isinstance(data, dict):
        raise DomainError('fields', '标记观测须为对象')
    chosen = cycle_id if cycle_id is not None else data.get('cycle')
    cycle_id = identifier(chosen)
    items = _observations(data)
    spec, revision = load_wafer(conn, wafer_id)
    expected = data.get('expected')
    events.check_revision(expected, revision)
    timeline = replay_timeline(spec, events.read(conn, wafer_id))
    state = timeline.cycle(cycle_id)
    before = state['lock']
    # 先在内存副本上试算整批观测；任一条冲突都不落任何事件。
    trial = {'id': state['id'], 'wafer': state.get('wafer'), 'poses': state['poses'],
             'markers': state['markers'], 'observations': [dict(item) for item in state['observations']],
             'lock': state['lock'], 'revision': state['revision']}
    fresh = []
    for item in items:
        if cycles.duplicate(trial, item['marker'], item['device']):
            continue
        cycles.record(trial, item['marker'], item['device'], revision + 1 + len(fresh))
        fresh.append(item)
    if revision + len(fresh) > events.LIMIT:
        raise DomainError('limit', '本片现场记录已达上限', 409)
    order = revision + 1
    for item in fresh:
        events.append(conn, wafer_id, order, 'mark', f'{cycle_id}:{item["marker"]}:{order}',
                      {'cycle': cycle_id, 'marker': item['marker'], 'device': item['device']})
        order += 1
    now = order - 1
    return {'revision': now, 'duplicate': not fresh, 'added': len(fresh),
            'skipped': len(items) - len(fresh),
            'newly_locked': before is None and trial['lock'] is not None,
            'state': _view(trial)}


def hold(conn, wafer_id, data):
    fields(data, 'expected', 'id', 'site', 'reason')
    site = point(data['site'])
    hold_id = identifier(data['id'])
    body = {'id': hold_id, 'site': list(site), 'reason': reason(data['reason'])}
    timeline, revision = current(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    old = events.find(conn, wafer_id, 'hold', hold_id)
    if old:
        if old != body:
            raise DomainError('hold_conflict', '复核编号已用于另一条现场记录', 409)
        return {'revision': revision, 'duplicate': True}
    timeline.cell(site)
    timeline.apply_hold(body, revision + 1)
    events.append(conn, wafer_id, revision + 1, 'hold', hold_id, body)
    return {'revision': revision + 1, 'duplicate': False}
