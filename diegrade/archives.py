from . import cycles, events
from .batches import map_sites
from .codec import decode, digest, encode
from .errors import DomainError
from .geometry import build_mask, point
from .sorter import assignments
from .timeline import Timeline, replay_timeline
from .validation import fields, identifier, integer
from .wafers import load_wafer

FORMAT = 'diegrade.release.2'
LEGACY = frozenset(('diegrade.release.1',))


def load_release(conn, wafer_id, revision):
    stored = conn.execute('SELECT body FROM releases WHERE wafer=? AND revision=?', (wafer_id, revision)).fetchone()
    if not stored:
        raise DomainError('release_missing', '分选版不存在', 404)
    return decode(stored[0])


def snapshot(cycle):
    found = cycles.result(cycle)
    lock = cycle['lock']
    return {'id': cycle['id'], 'wafer': cycle.get('wafer'),
            'poses': [dict(item) for item in cycle['poses']],
            'markers': {name: list(site) for name, site in cycle['markers'].items()},
            'state': found['state'],
            'observations': [{'marker': item['marker'], 'device': list(item['device']),
                              'revision': item['revision']} for item in cycle['observations']],
            'pose': dict(lock['pose']) if lock else None,
            'lock_revision': lock['revision'] if lock else None,
            'lock_observations': [{'marker': item['marker'], 'device': list(item['device']),
                                   'revision': item['revision']} for item in lock['observations']] if lock else []}


def cycles_snapshot(timeline):
    return [snapshot(state) for state in timeline.cycle_log]


def _replay(spec, evidence):
    timeline = Timeline(spec)
    usage = []
    for event in evidence:
        if event['kind'] == 'batch':
            usage.append(describe(timeline, event))
        timeline.apply(event)
    return timeline, usage


def seal(conn, wafer_id, data):
    fields(data, 'expected')
    spec, revision = load_wafer(conn, wafer_id)
    events.check_revision(data['expected'], revision)
    try:
        return load_release(conn, wafer_id, revision)
    except DomainError as exc:
        if exc.code != 'release_missing':
            raise
    evidence = events.read(conn, wafer_id)
    timeline, usage = _replay(spec, evidence)
    rows = assignments(timeline)
    body = {'format': FORMAT, 'revision': revision, 'wafer': spec, 'events': evidence,
            'cycles': cycles_snapshot(timeline), 'usage': usage, 'rows': rows}
    release = {'payload': body, 'sha256': digest(body)}
    conn.execute('INSERT INTO releases VALUES (?, ?, ?)', (wafer_id, revision, encode(release)))
    return release


def _snapshots(value):
    if value is None:
        return {}
    if not isinstance(value, list) or len(value) > 128:
        raise DomainError('release_cycles', '装夹周期快照结构不正确', 409)
    result = {}
    for item in value:
        if not isinstance(item, dict) or 'id' not in item:
            raise DomainError('release_cycles', '装夹周期快照结构不正确', 409)
        cycle_id = identifier(item['id'])
        if cycle_id in result:
            raise DomainError('release_cycles', '装夹周期编号在封存件中重复', 409)
        result[cycle_id] = item
    return result


def _observation(item, physical):
    if not isinstance(item, dict):
        raise DomainError('release_cycles', '标记观测结构不正确', 409)
    marker, device, revision = item.get('marker'), item.get('device'), item.get('revision')
    identifier(marker)
    return {'marker': marker, 'device': list(point(device, '标记设备坐标')),
            'revision': integer(revision, 0, 128, '现场代次')}


def _carried_cycle(item, physical, wafer_id):
    """从随件快照取出周期配置；配置须自洽，锁定内容另行核对。"""
    poses = item.get('poses')
    markers = item.get('markers')
    if item.get('wafer') not in (None, wafer_id):
        raise DomainError('release_replay', '周期快照所属晶圆不符', 409)
    if not isinstance(poses, list) or not 1 <= len(poses) <= cycles.MAX_POSES:
        raise DomainError('release_cycles', '周期允许位姿缺失', 409)
    normalized = [cycles.pose_spec(value) for value in poses]
    if not isinstance(markers, dict) or not 1 <= len(markers) <= cycles.MAX_MARKERS:
        raise DomainError('release_cycles', '周期标记缺失', 409)
    sites = {}
    for name, value in markers.items():
        identifier(name)
        site = point(value, '标记物理坐标')
        if site not in physical:
            raise DomainError('release_cycles', f'标记 {name} 不在物理掩模内', 409)
        sites[name] = list(site)
    observations = [_observation(value, physical) for value in item.get('lock_observations') or []]
    all_observations = None if 'observations' not in item else [
        _observation(value, physical) for value in item.get('observations') or []]
    if item.get('state') == 'confirmed' and item.get('pose') is None:
        raise DomainError('release_replay', '周期快照标记为已确认却缺少锁定位姿', 409)
    if item.get('state') in ('undetermined', 'inconsistent', 'open') and item.get('pose') is not None:
        raise DomainError('release_replay', '周期快照未确认却记录了锁定位姿', 409)
    specified = item.get('state')
    if all_observations:
        found = cycles.solve(normalized, sites, [{'marker': o['marker'], 'device': o['device']}
                                                 for o in all_observations])
        if specified is not None and found['state'] != specified:
            raise DomainError('release_replay', '周期快照状态与标记证据不符', 409)
    pose = item.get('pose')
    if pose is None:
        return {'id': item['id'], 'poses': normalized, 'markers': sites, 'pose': None,
                'observations': observations, 'all_observations': all_observations}
    if not isinstance(pose, dict):
        raise DomainError('release_cycles', '锁定位姿结构不正确', 409)
    candidate = {'offset': list(point(pose.get('offset'), '平移')), 'turn': pose.get('turn'),
                 'mirror': pose.get('mirror')}
    integer(candidate['turn'], 0, 3, '逆时针四分之一圈数')
    if type(candidate['mirror']) is not bool:
        raise DomainError('release_cycles', '锁定位姿的镜像值不合法', 409)
    if not observations:
        raise DomainError('release_replay', '锁定周期缺少确认时的原始标记', 409)
    found = cycles.solve(normalized, sites, [{'marker': o['marker'], 'device': o['device']} for o in observations])
    if found['state'] != 'confirmed' or found['candidates'][0] != candidate:
        raise DomainError('release_replay', '确认时的原始标记不支持所记录的锁定位姿', 409)
    if all_observations:
        for item_obs in all_observations:
            if not cycles.fits(candidate, sites[item_obs['marker']], item_obs['device']):
                raise DomainError('release_replay', '锁定后的标记观测与锁定位姿冲突', 409)
    return {'id': item['id'], 'poses': normalized, 'markers': sites, 'pose': candidate,
            'observations': observations, 'lock_revision': item.get('lock_revision'),
            'state': item.get('state'), 'all_observations': all_observations}


def _pairs(items):
    return [{key: item[key] for key in ('marker', 'device')} for item in items]


def _check_snapshot(carried, actual, wafer_id):
    if carried.get('wafer') not in (None, wafer_id):
        raise DomainError('release_replay', '周期快照所属晶圆不符', 409)
    if carried['poses'] != actual['poses']:
        raise DomainError('release_replay', '周期允许位姿与随件快照不符', 409)
    if {name: tuple(site) for name, site in carried['markers'].items()} != \
            {name: tuple(site) for name, site in actual['markers'].items()}:
        raise DomainError('release_replay', '周期标记坐标与随件快照不符', 409)
    if carried.get('all_observations') is not None and \
            _pairs(actual['observations']) != _pairs(carried['all_observations']):
        raise DomainError('release_replay', '标记观测与随件快照不符', 409)
    if carried.get('all_observations') is None and carried.get('state') is not None:
        found = cycles.result(actual)
        if found['state'] != carried['state']:
            raise DomainError('release_replay', '周期状态与标记证据不符', 409)
    lock = actual['lock']
    if carried['pose'] is None:
        if lock is not None:
            raise DomainError('release_replay', '随件快照未记录锁定位姿', 409)
        return
    if lock is None:
        raise DomainError('release_replay', '现场记录未产生随件快照所记的锁定位姿', 409)
    if lock['pose'] != carried['pose']:
        raise DomainError('release_replay', '锁定位姿与随件快照不符', 409)
    if _pairs(lock['observations']) != _pairs(carried['observations']):
        raise DomainError('release_replay', '确认时的原始标记与随件快照不符', 409)


def recompute(body):
    """用随件周期配置、确认时的原始标记与量测批次独立复算。"""
    if body.get('format') in LEGACY:
        timeline = replay_timeline(body['wafer'], body['events'])
        return timeline, []
    if body.get('format') != FORMAT:
        raise DomainError('release_digest', '分选版结构或摘要不一致', 409)
    spec = body['wafer']
    physical, _ = build_mask(spec['spans'], spec['reserved'])
    carried = _snapshots(body.get('cycles'))
    timeline = Timeline(spec)
    usage = []
    seen_cycles = set()
    for event in body['events']:
        kind = event['kind']
        if kind == 'cycle':
            body_value = event['body']
            cycle_id = body_value.get('id') if isinstance(body_value, dict) else None
            if cycle_id not in carried:
                raise DomainError('release_replay', '现场登记了随件快照未包含的装夹周期', 409)
            timeline.apply(event)
            seen_cycles.add(cycle_id)
        elif kind == 'mark':
            body_value = event['body']
            cycle_id = body_value.get('cycle') if isinstance(body_value, dict) else None
            if cycle_id not in carried:
                raise DomainError('release_replay', '标记观测引用了随件快照未包含的周期', 409)
            timeline.apply(event)
        elif kind == 'batch':
            usage.append(describe(timeline, event))
            timeline.apply(event)
        else:
            timeline.apply(event)
    if seen_cycles != set(carried):
        raise DomainError('release_replay', '随件周期快照缺少原始登记记录', 409)
    for state in timeline.cycle_log:
        _check_snapshot(carried[state['id']], state, spec['id'])
    sealed = body.get('usage')
    if sealed is not None and sealed != usage:
        raise DomainError('release_replay', '批次位姿采用与封存记录不符', 409)
    return timeline, usage


def describe(timeline, event):
    batch = event['body']
    mapping = timeline.mapping_for(batch)
    sites = [list(site) for site, _ in map_sites(batch, timeline.physical, timeline.reserved, mapping)]
    return {'id': batch['id'], 'kind': batch['kind'], 'cycle': batch.get('cycle'),
            'pose': {'offset': list(mapping['offset']), 'turn': mapping['turn'], 'mirror': mapping['mirror']},
            'sites': sites}


def verify(release):
    body = release['payload']
    if digest(body) != release['sha256']:
        raise DomainError('release_digest', '分选版结构或摘要不一致', 409)
    timeline, usage = recompute(body)
    computed = assignments(timeline)
    if computed != body['rows']:
        raise DomainError('release_replay', '现场复算与已归档分选计划不一致', 409)
    result = {'rows': len(computed), 'sha256': release['sha256'], 'revision': body['revision'],
              'verified': True, 'batches': usage}
    if body.get('format') == FORMAT:
        result['cycles'] = [snapshot(state) for state in timeline.cycle_log]
    else:
        result['cycles'] = []
    return result


def verify_package(data):
    """接收方独立核验随件的封存包，不读取本机现场记录。"""
    if not isinstance(data, dict):
        raise DomainError('fields', '封存包须为对象')
    body = data.get('payload')
    if not isinstance(body, dict):
        raise DomainError('release_structure', '封存包缺少载荷', 422)
    spec = body.get('wafer')
    if not isinstance(spec, dict) or not isinstance(spec.get('id'), str):
        raise DomainError('release_structure', '封存包未注明所属晶圆', 422)
    identifier(spec['id'])
    expected = data.get('wafer')
    if expected is not None and expected != spec['id']:
        raise DomainError('release_wafer', '封存包所属晶圆与接收方不符', 409)
    package = {'payload': body, 'sha256': data.get('sha256')}
    result = verify(package)
    return {**result, 'wafer': spec['id']}
