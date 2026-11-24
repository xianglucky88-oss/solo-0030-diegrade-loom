"""装夹周期的对准标记与可行位姿。

一个装夹周期固定所属晶圆、允许的旋转/镜像组合，以及每个对准标记在物理
掩模上的坐标。标记观测按标记编号与设备坐标分次送达；对每个允许的朝向，
全部观测必须由同一个平移解释，才算该朝向可行。可行朝向恰好一个时锁定；
没有可行朝向时指出观测不一致；仍有多个可行朝向时保持未确定。

位姿求解只使用标记观测，绝不参考量测批次能否入库。批次引用周期时一律
按锁定位姿归一，批次自带位姿与锁定值冲突则拒绝来件。
"""

from .errors import DomainError
from .frames import orient
from .geometry import point
from .validation import identifier, integer

MAX_POSES = 8
MAX_MARKERS = 64


def pose_spec(value):
    """规范化单个允许位姿；供接收方复算使用。"""
    return _pose(value)


def _pose(value):
    if isinstance(value, dict):
        for key in ('turn', 'rotation', 'degrees', 'angle'):
            if key in value:
                turn = value[key]
                break
        else:
            raise DomainError('cycle_pose', '位姿须给出旋转')
        mirror = value.get('mirror', value.get('mirrored', False))
    elif isinstance(value, list) and len(value) == 2:
        turn, mirror = value
    else:
        raise DomainError('cycle_pose', '每个允许位姿须为 {turn, mirror} 或 [turn, mirror]')
    if type(turn) is int and turn in (90, 180, 270):
        turn = turn // 90
    integer(turn, 0, 3, '逆时针四分之一圈数')
    if type(mirror) is not bool:
        raise DomainError('cycle_pose', '镜像值须为布尔值')
    return {'turn': turn, 'mirror': mirror}


def _pose_list(spec):
    for key in ('poses', 'orientations', 'allowed', 'combinations'):
        if key in spec:
            source = spec[key]
            break
    else:
        turns = spec.get('turns', spec.get('rotations'))
        mirrors = spec.get('mirrors', [spec.get('mirror', False)])
        if not isinstance(mirrors, list):
            mirrors = [mirrors]
        if not isinstance(turns, list) or not isinstance(mirrors, list):
            raise DomainError('cycle_pose', '旋转与镜像组合须为数组')
        source = [{'turn': turn, 'mirror': mirror} for turn in turns for mirror in mirrors]
    if not isinstance(source, list) or not 1 <= len(source) <= MAX_POSES:
        raise DomainError('cycle_pose', f'允许位姿须为 1–{MAX_POSES} 个')
    seen, result = set(), []
    for value in source:
        if type(value) is int:
            value = {'turn': value, 'mirror': False}
        item = _pose(value)
        key = (item['turn'], item['mirror'])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return sorted(result, key=lambda item: (item['turn'], item['mirror']))


def _marker_site(value):
    if isinstance(value, dict):
        if 'site' in value:
            return point(value['site'], '标记物理坐标')
        if 'physical' in value:
            return point(value['physical'], '标记物理坐标')
        if 'x' in value and 'y' in value:
            return point([value['x'], value['y']], '标记物理坐标')
        raise DomainError('cycle_marker', '标记对象须给出 site、physical 或 x/y 坐标')
    return point(value, '标记物理坐标')


def _markers(spec):
    source = None
    for key in ('markers', 'alignment_marks', 'alignment', 'marks'):
        if key in spec:
            source = spec[key]
            break
    if isinstance(source, dict):
        items = list(source.items())
    elif isinstance(source, list):
        items = []
        for value in source:
            if isinstance(value, dict) and ('id' in value or 'marker' in value or 'name' in value):
                name = value.get('id', value.get('marker', value.get('name')))
                items.append((name, value))
            elif isinstance(value, list) and len(value) == 2:
                items.append((value[0], value[1]))
            else:
                raise DomainError('cycle_marker', '标记须为 [编号, 物理坐标] 或含编号与坐标的对象')
    else:
        raise DomainError('fields', '装夹周期须给出各标记的物理坐标')
    if not 1 <= len(items) <= MAX_MARKERS:
        raise DomainError('cycle_marker', f'标记须为 1–{MAX_MARKERS} 个')
    markers, sites = {}, set()
    for name, value in items:
        identifier(name)
        if name in markers:
            raise DomainError('cycle_marker', f'标记编号 {name} 重复')
        site = _marker_site(value)
        if site in sites:
            raise DomainError('cycle_marker', f'标记 {name} 与其他标记占据同一物理点')
        sites.add(site)
        markers[name] = list(site)
    return markers


def prepare(value, physical):
    """校验并规范化周期登记正文；标记须落在本片物理掩模内。"""
    if not isinstance(value, dict) or 'id' not in value:
        raise DomainError('fields', '装夹周期须为含编号的对象')
    cycle_id = identifier(value['id'])
    waist = value.get('wafer', value.get('wafer_id'))
    if waist is not None:
        identifier(waist)
    spec = {'id': cycle_id, 'poses': _pose_list(value), 'markers': _markers(value)}
    for name, site in spec['markers'].items():
        if tuple(site) not in physical:
            raise DomainError('cycle_marker', f'标记 {name} 不在物理掩模内')
    if waist is not None:
        spec['wafer'] = waist
    return spec


def solve(poses, markers, observations):
    """求全部可行朝向及其唯一平移；每个朝向只能用一个平移解释所有观测。"""
    if not observations:
        return {'state': 'open',
                'candidates': [{'turn': pose['turn'], 'mirror': pose['mirror']} for pose in poses]}
    feasible = []
    for pose in poses:
        offset = None
        for observation in observations:
            site = markers.get(observation['marker'])
            if site is None:
                offset = None
                break
            dx, dy = orient(pose, *observation['device'])
            candidate = (site[0] - dx, site[1] - dy)
            if offset is None:
                offset = candidate
            elif offset != candidate:
                offset = None
                break
        if offset is not None:
            feasible.append({'turn': pose['turn'], 'mirror': pose['mirror'], 'offset': list(offset)})
    if not feasible:
        state = 'inconsistent'
    elif len(feasible) == 1:
        state = 'confirmed'
    else:
        state = 'undetermined'
    return {'state': state, 'candidates': feasible}


def state(cycle_id, spec, revision):
    """新建一个尚未收到观测的周期状态。"""
    return {'id': cycle_id, 'wafer': spec.get('wafer'),
            'poses': [dict(item) for item in spec['poses']],
            'markers': {name: list(site) for name, site in spec['markers'].items()},
            'observations': [], 'lock': None, 'revision': revision}


def config(cycle):
    return {'id': cycle['id'], 'poses': [dict(item) for item in cycle['poses']],
            'markers': {name: list(site) for name, site in cycle['markers'].items()}}


def result(cycle):
    return solve(cycle['poses'], cycle['markers'], cycle['observations'])


def duplicate(cycle, marker, device):
    return any(item['marker'] == marker and tuple(item['device']) == tuple(device)
               for item in cycle['observations'])


def record(cycle, marker, device, revision):
    """把一条观测纳入周期状态；返回求解结果。

    同一标记以同一设备坐标重送不重复增加证据。锁定之后，与锁定位姿冲突的
    观测一律拒绝；与锁定位姿相符的新观测只作补充证据，不改动锁定内容。
    """
    site = cycle['markers'].get(marker)
    if site is None:
        raise DomainError('cycle_marker', f'周期未登记标记 {marker}')
    if duplicate(cycle, marker, device):
        return result(cycle)
    if cycle['lock'] is not None and not fits(cycle['lock']['pose'], site, device):
        raise DomainError('cycle_observation_conflict', '标记观测与已锁定位姿冲突', 409)
    cycle['observations'].append({'marker': marker, 'device': list(device), 'revision': revision})
    found = result(cycle)
    if cycle['lock'] is None and found['state'] == 'confirmed':
        candidate = found['candidates'][0]
        cycle['lock'] = {'pose': {'offset': list(candidate['offset']), 'turn': candidate['turn'],
                                  'mirror': candidate['mirror']},
                         'revision': revision,
                         'observations': [{'marker': item['marker'], 'device': list(item['device']),
                                           'revision': item['revision']} for item in cycle['observations']]}
    return found


def fits(lock_value, site, device):
    """观测是否与已锁定位姿相符。"""
    dx, dy = orient(lock_value, *device)
    return (dx + lock_value['offset'][0], dy + lock_value['offset'][1]) == tuple(site)


def pose(cycle):
    return dict(cycle['lock']['pose']) if cycle['lock'] else None


def observation(value):
    """把一条标记观测规范成 {marker, device}。"""
    if not isinstance(value, dict):
        raise DomainError('cycle_mark', '标记观测须为对象')
    marker = value.get('marker', value.get('id', value.get('name')))
    identifier(marker)
    if 'device' in value:
        device = point(value['device'], '标记设备坐标')
    elif 'device_x' in value and 'device_y' in value:
        device = point([value['device_x'], value['device_y']], '标记设备坐标')
    elif 'x' in value and 'y' in value:
        device = point([value['x'], value['y']], '标记设备坐标')
    else:
        raise DomainError('cycle_mark', '标记观测须给出设备坐标')
    return {'marker': marker, 'device': list(device)}


def view(cycle):
    found = result(cycle)
    lock = cycle['lock']
    return {'id': cycle['id'], 'wafer': cycle.get('wafer'),
            'poses': [dict(item) for item in cycle['poses']],
            'markers': {name: list(site) for name, site in cycle['markers'].items()},
            'state': found['state'], 'candidates': found['candidates'],
            'locked': lock is not None,
            'pose': dict(lock['pose']) if lock else None,
            'lock_revision': lock['revision'] if lock else None,
            'lock_observations': [dict(item) for item in lock['observations']] if lock else [],
            'observations': [{'marker': item['marker'], 'device': list(item['device']),
                              'revision': item['revision']} for item in cycle['observations']]}
