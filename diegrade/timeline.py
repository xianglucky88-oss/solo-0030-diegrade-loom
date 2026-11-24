from .batches import map_sites
from .errors import DomainError
from .geometry import build_mask, is_edge, order
from .grading import FINAL, grade


class Timeline:
    def __init__(self, spec):
        self.spec = spec
        self.physical, self.reserved = build_mask(spec['spans'], spec['reserved'])
        self.active = {}
        self.history = {}
        self.holds = {}

    def apply(self, event):
        if event['kind'] == 'batch':
            self.apply_batch(event['body'], event['revision'])
        elif event['kind'] == 'suspend':
            self.apply_suspend(event['body'], event['revision'])
        else:
            self.apply_hold(event['body'], event['revision'])

    def apply_batch(self, batch, revision):
        repeated = batch['kind'] == 'repeat'
        decisions = []
        for site, record in map_sites(batch, self.physical, self.reserved):
            older = self.active.get(site)
            if repeated:
                if not older or older['batch'] != record['parent'] or self.state(site) != 'review':
                    raise DomainError('repeat_parent', f'格点 {site} 的父批次不是当前待复测证据', 409)
            elif older:
                raise DomainError('screen_exists', f'格点 {site} 已有首测', 409)
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), repeated)
            decisions.append((site, {**result, 'event': 'measurement', 'batch': batch['id'], 'revision': revision,
                                     'frame': batch['frame'], 'kind': batch['kind'], 'record': record}))
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

    def apply_suspend(self, body, revision):
        key = (body['card'], body['tip'], body['segment'])
        for site in body['sites']:
            site = tuple(site)
            active = self.active.get(site)
            record = active['record'] if active and self.state(site) in FINAL else None
            if not record or (record['card'], record['tip'], record['segment']) != key:
                raise DomainError('suspend_state', f'格点 {site} 的当前终档证据不属于该停用组合', 409)
            entry = {'event': 'suspend', 'id': body['id'], 'why': body['reason'], 'revision': revision,
                     'batch': active['batch'], 'card': body['card'], 'tip': body['tip'], 'segment': body['segment']}
            self.holds[site] = entry
            self.history[site].append(entry)

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
