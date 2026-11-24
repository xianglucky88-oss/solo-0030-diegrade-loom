from .batches import map_sites
from .errors import DomainError
from .geometry import build_mask, is_edge, order
from .grading import FINAL, grade


class Timeline:
    def __init__(self, spec):
        self.spec = spec
        self.conditions = spec.get('conditions')
        self.physical, self.reserved = build_mask(spec['spans'], spec['reserved'])
        self.active = {}
        self.history = {}
        self.holds = {}

    def apply(self, event):
        if event['kind'] == 'batch':
            self.apply_batch(event['body'], event['revision'])
        else:
            self.apply_hold(event['body'], event['revision'])

    def apply_batch(self, batch, revision):
        repeated = batch['kind'] == 'repeat'
        condition = batch.get('condition')
        if self.conditions:
            if condition not in self.conditions:
                raise DomainError('condition', '批次须指定本片登记的量测条件')
        elif condition is not None:
            raise DomainError('condition', '单条件晶圆的批次不指定量测条件')
        decisions = []
        for site, record in map_sites(batch, self.physical, self.reserved):
            older = self.entry(site, condition)
            if repeated:
                if not older or older['batch'] != record['parent'] or self.condition_state(site, condition) != 'review':
                    raise DomainError('repeat_parent', f'格点 {site} 的父批次不是当前待复测证据', 409)
            elif older:
                raise DomainError('screen_exists', f'格点 {site} 已有首测', 409)
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), repeated)
            decision = {**result, 'event': 'measurement', 'batch': batch['id'], 'revision': revision,
                        'frame': batch['frame'], 'kind': batch['kind'], 'record': record}
            if self.conditions:
                decision['condition'] = condition
            decisions.append((site, decision))
        for site, result in decisions:
            if self.conditions:
                self.active.setdefault(site, {})[condition] = result
                holds = self.holds.get(site)
                if holds:
                    holds.pop(condition, None)
                    if not holds:
                        del self.holds[site]
            else:
                self.active[site] = result
                self.holds.pop(site, None)
            self.history.setdefault(site, []).append(result)
        return decisions

    def apply_hold(self, body, revision):
        site = tuple(body['site'])
        condition = body.get('condition')
        if self.conditions:
            if condition not in self.conditions:
                raise DomainError('condition', '复核须指定本片登记的量测条件')
            if self.condition_state(site, condition) not in FINAL:
                raise DomainError('hold_state', '只有已经判档的条件可重新开启复测', 409)
            hold = {'event': 'hold', 'why': body['reason'], 'revision': revision,
                    'batch': self.active[site][condition]['batch'], 'condition': condition}
            self.holds.setdefault(site, {})[condition] = hold
        else:
            if self.condition_state(site) not in FINAL:
                raise DomainError('hold_state', '只有已经判档的格点可重新开启复测', 409)
            hold = {'event': 'hold', 'why': body['reason'], 'revision': revision, 'batch': self.active[site]['batch']}
            self.holds[site] = hold
        self.history[site].append(hold)

    def entry(self, site, condition=None):
        if self.conditions:
            return self.active.get(site, {}).get(condition)
        return self.active.get(site)

    def condition_state(self, site, condition=None):
        if site in self.reserved:
            return 'reserved'
        if self.conditions:
            if condition in self.holds.get(site, {}):
                return 'review'
            entry = self.active.get(site, {}).get(condition)
        else:
            if site in self.holds:
                return 'review'
            entry = self.active.get(site)
        return entry['state'] if entry else 'missing'

    def state(self, site):
        if not self.conditions:
            return self.condition_state(site)
        if site in self.reserved:
            return 'reserved'
        states = [self.condition_state(site, condition) for condition in self.conditions]
        if 'missing' in states:
            return 'missing'
        if 'review' in states:
            return 'review'
        return self.combine(site)

    def combine(self, site):
        entries = [self.active[site][condition] for condition in self.conditions]
        states = [entry['state'] for entry in entries]
        if 'reject' in states:
            return 'reject'
        first, second = (entry['record']['margin_mv'] for entry in entries)
        if abs(first - second) > self.spec['margin_gap']:
            return 'reject'
        return 'a' if all(state == 'a' for state in states) else 'b'

    def grid(self):
        return [self.cell(site) for site in order(self.physical)]

    def cell(self, site):
        if site not in self.physical:
            raise DomainError('site_missing', '格点不属于本片晶圆', 404)
        cell = {'site': list(site), 'edge': is_edge(site, self.physical), 'state': self.state(site),
                'batch': None if self.conditions else self.active.get(site, {}).get('batch')}
        if self.conditions and site not in self.reserved:
            cell['conditions'] = {condition: self.condition_cell(site, condition) for condition in self.conditions}
        return cell

    def condition_cell(self, site, condition):
        entry = self.active.get(site, {}).get(condition)
        record = entry['record'] if entry else {}
        return {'state': self.condition_state(site, condition),
                'batch': entry['batch'] if entry else None,
                'margin_mv': record.get('margin_mv') if record.get('contact') == 'ok' else None}


def replay_timeline(spec, events):
    timeline = Timeline(spec)
    for event in events:
        timeline.apply(event)
    return timeline
