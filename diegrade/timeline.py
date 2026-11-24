from . import cycles
from .batches import map_sites
from .errors import DomainError
from .frames import same
from .geometry import build_mask, is_edge, order
from .grading import FINAL, grade


class Timeline:
    def __init__(self, spec):
        self.spec = spec
        self.physical, self.reserved = build_mask(spec['spans'], spec['reserved'])
        self.active = {}
        self.history = {}
        self.holds = {}
        self.cycles = {}
        self.cycle_log = []

    def apply(self, event):
        kind = event['kind']
        if kind == 'batch':
            self.apply_batch(event['body'], event['revision'])
        elif kind == 'hold':
            self.apply_hold(event['body'], event['revision'])
        elif kind == 'cycle':
            self.apply_cycle(event['body'], event['revision'])
        elif kind == 'mark':
            self.apply_mark(event['body'], event['revision'])
        else:
            raise DomainError('event_kind', f'未知现场记录类型 {kind}')

    def apply_cycle(self, body, revision):
        if not isinstance(body, dict) or 'id' not in body:
            raise DomainError('cycle', '周期记录缺少编号')
        if body['id'] in self.cycles:
            raise DomainError('cycle_conflict', '装夹周期编号已登记', 409)
        spec = cycles.prepare(body, self.physical)
        state = cycles.state(spec['id'], spec, revision)
        self.cycles[spec['id']] = state
        self.cycle_log.append(state)

    def apply_mark(self, body, revision):
        if not isinstance(body, dict):
            raise DomainError('cycle_mark', '标记观测须为对象')
        cycle_id = body.get('cycle')
        state = self.cycles.get(cycle_id)
        if state is None:
            raise DomainError('cycle_missing', f'没有装夹周期 {cycle_id}', 404)
        item = cycles.observation(body)
        if cycles.duplicate(state, item['marker'], item['device']):
            return
        cycles.record(state, item['marker'], item['device'], revision)

    def cycle(self, cycle_id):
        state = self.cycles.get(cycle_id)
        if state is None:
            raise DomainError('cycle_missing', f'没有装夹周期 {cycle_id}', 404)
        return state

    def mapping_for(self, batch):
        """批次实际采用的坐标变换；引用周期时只认锁定位姿。"""
        cycle_id = batch.get('cycle')
        if cycle_id is None:
            if batch.get('frame') is None:
                raise DomainError('frame', '批次没有可用坐标系')
            return batch['frame']
        state = self.cycle(cycle_id)
        found = cycles.result(state)
        if found['state'] == 'inconsistent':
            raise DomainError('cycle_inconsistent', '标记观测不一致，不存在可行位姿，拒绝该周期的量测', 409)
        if found['state'] != 'confirmed' or state['lock'] is None:
            raise DomainError('cycle_unconfirmed', '装夹位姿尚未确定，拒绝该周期的量测', 409)
        locked = cycles.pose(state)
        supplied = batch.get('frame')
        if supplied is not None and not same(supplied, locked):
            raise DomainError('cycle_pose_conflict', '批次自带位姿与锁定位姿冲突', 409)
        return locked

    def apply_batch(self, batch, revision):
        repeated = batch['kind'] == 'repeat'
        mapping = self.mapping_for(batch)
        decisions = []
        for site, record in map_sites(batch, self.physical, self.reserved, mapping):
            older = self.active.get(site)
            if repeated:
                if not older or older['batch'] != record['parent'] or self.state(site) != 'review':
                    raise DomainError('repeat_parent', f'格点 {site} 的父批次不是当前待复测证据', 409)
            elif older:
                raise DomainError('screen_exists', f'格点 {site} 已有首测', 409)
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), repeated)
            decisions.append((site, {**result, 'event': 'measurement', 'batch': batch['id'], 'revision': revision,
                                     'frame': mapping, 'cycle': batch.get('cycle'), 'kind': batch['kind'],
                                     'record': record}))
        for site, result in decisions:
            self.active[site] = result
            self.holds.pop(site, None)
            self.history.setdefault(site, []).append(result)
        return decisions

    def apply_hold(self, body, revision):
        site = tuple(body['site'])
        if self.state(site) not in FINAL:
            raise DomainError('hold_state', '只有已经判档的格点可重新开启复测', 409)
        hold = {'event': 'hold', 'why': body['reason'], 'revision': revision, 'batch': self.active[site]['batch']}
        self.holds[site] = hold
        self.history[site].append(hold)

    def state(self, site):
        if site in self.reserved:
            return 'reserved'
        if site in self.holds:
            return 'review'
        return self.active[site]['state'] if site in self.active else 'missing'

    def grid(self):
        return [self.cell(site) for site in order(self.physical)]

    def cell(self, site):
        if site not in self.physical:
            raise DomainError('site_missing', '格点不属于本片晶圆', 404)
        return {'site': list(site), 'edge': is_edge(site, self.physical), 'state': self.state(site),
                'batch': self.active.get(site, {}).get('batch')}


def replay_timeline(spec, events):
    timeline = Timeline(spec)
    for event in events:
        timeline.apply(event)
    return timeline
