from .batches import map_sites
from .errors import DomainError
from .frames import normalize
from .geometry import build_mask, is_edge, order
from .grading import FINAL, grade


def identity(batch_id, record):
    """一条原始量测行的身份：批次号加设备原坐标，位姿变化不改变它。"""
    return [batch_id, record['x'], record['y']]


class Timeline:
    def __init__(self, spec):
        self.spec = spec
        self.physical, self.reserved = build_mask(spec['spans'], spec['reserved'])
        self.active = {}
        self.history = {}
        self.holds = {}
        self.batches = {}
        self.corrected = set()
        self.reopened = {}

    def apply(self, event):
        if event['kind'] == 'batch':
            self.apply_batch(event['body'], event['revision'])
        elif event['kind'] == 'correction':
            self.apply_correction(event['body'], event['revision'])
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
                lineage = [list(item) for item in older['lineage']] + [identity(batch['id'], record)]
            elif older:
                raise DomainError('screen_exists', f'格点 {site} 已有首测', 409)
            else:
                lineage = [identity(batch['id'], record)]
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), repeated)
            decisions.append((site, {**result, 'event': 'measurement', 'batch': batch['id'], 'revision': revision,
                                     'frame': batch['frame'], 'kind': batch['kind'], 'record': record,
                                     'lineage': lineage}))
        self.batches[batch['id']] = batch
        for site, result in decisions:
            self.active[site] = result
            self.holds.pop(site, None)
            self.reopened.pop(site, None)
            self.history.setdefault(site, []).append(result)
        return decisions

    def apply_correction(self, body, revision):
        batch_id = body['batch']
        batch = self.batches.get(batch_id)
        if batch is None:
            raise DomainError('correction_batch', '本片没有登记过该批次', 404)
        if batch['kind'] != 'screen':
            raise DomainError('correction_kind', '只能更正首测批次的设备位姿')
        if batch_id in self.corrected:
            raise DomainError('correction_exists', '该批次已经登记过更正', 409)
        old_frame, new_frame = batch['frame'], body['frame']
        old_map, new_map = {}, {}
        for record in batch['rows']:
            key = (record['x'], record['y'])
            old_map[key] = normalize(old_frame, record['x'], record['y'])
            new_map[key] = normalize(new_frame, record['x'], record['y'])
        occupied = set(old_map.values())
        targets = set(new_map.values())
        for site in targets:
            if site not in self.physical or site in self.reserved:
                raise DomainError('correction_site', f'更正后的格点 {site} 落在掩模外或保留点上')
        if len(targets) != len(new_map) or targets != occupied:
            raise DomainError('correction_set', '更正后须在原批次已占的物理点集合内一一重排原行')
        if new_map == old_map:
            raise DomainError('correction_noop', '更正后的位姿与已记录位姿在物理格点上没有区别')

        inverted_old = {site: key for key, site in old_map.items()}
        superseded = {}
        for site, active in self.active.items():
            lineage = active.get('lineage') or []
            if len(lineage) < 2:
                continue
            root_batch, root_x, root_y = lineage[0]
            if root_batch == batch_id and new_map.get((root_x, root_y)) != site:
                superseded[site] = [list(item) for item in lineage]

        changes = []
        for key, site in new_map.items():
            tag = [batch_id, key[0], key[1]]
            current = self.active.get(site)
            lineage = (current or {}).get('lineage') or []
            if lineage and lineage[0] == tag and site not in superseded:
                if len(lineage) > 1:
                    continue  # 复测依赖的原行仍在此点成立，沿用原记录。
                if new_map[key] == old_map[key]:
                    continue  # 原归属未变的证据保持原状。
            record = next(item for item in batch['rows'] if (item['x'], item['y']) == key)
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), False)
            entry = {**result, 'event': 'correction', 'batch': batch_id, 'revision': revision,
                     'frame': new_frame, 'kind': 'screen', 'record': record,
                     'reason': body['reason'], 'lineage': [tag],
                     'previous': {'site': list(old_map[key]), 'frame': old_frame}}
            reopened = site in superseded
            if reopened:
                entry['reopened'] = True
                entry['superseded'] = superseded[site]
            self.active[site] = entry
            if reopened:
                self.reopened[site] = {'why': body['reason'], 'superseded': superseded[site]}
            if reopened or inverted_old[site] != key:
                self.history.setdefault(site, []).append(entry)
            changes.append((site, entry))
        self.corrected.add(batch_id)
        return changes

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
        if site in self.holds or site in self.reopened:
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
