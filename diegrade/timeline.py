from collections import Counter

from .batches import map_sites
from .errors import DomainError
from .frames import normalize
from .geometry import build_mask, is_edge, order
from .grading import FINAL, grade
from .probe_csv import scan


class Timeline:
    def __init__(self, spec):
        self.spec = spec
        self.physical, self.reserved = build_mask(spec['spans'], spec['reserved'])
        self.active = {}
        self.history = {}
        self.holds = {}
        self.consignments = {}

    def apply(self, event):
        kind = event['kind']
        if kind == 'batch':
            self.apply_batch(event['body'], event['revision'])
        elif kind == 'hold':
            self.apply_hold(event['body'], event['revision'])
        elif kind == 'consignment':
            self.apply_consignment(event['body'], event['revision'])
        else:
            self.apply_rework(event['body'], event['revision'])

    def record_measurement(self, site, record, result, revision, ref, frame, kind, extra=None):
        entry = {**result, 'event': 'measurement', 'batch': ref, 'revision': revision,
                 'frame': frame, 'kind': kind, 'record': record}
        if extra:
            entry.update(extra)
        self.active[site] = entry
        self.holds.pop(site, None)
        self.history.setdefault(site, []).append(entry)

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
            decisions.append((site, record, result))
        for site, record, result in decisions:
            self.record_measurement(site, record, result, revision, batch['id'], batch['frame'], batch['kind'])
        return [(site, result) for site, _, result in decisions]

    def apply_hold(self, body, revision):
        site = tuple(body['site'])
        if self.state(site) not in FINAL:
            raise DomainError('hold_state', '只有已经判档的格点可重新开启复测', 409)
        hold = {'event': 'hold', 'why': body['reason'], 'revision': revision, 'batch': self.active[site]['batch']}
        self.holds[site] = hold
        self.history[site].append(hold)

    def reviewable(self, site, parent):
        return self.state(site) == 'review' and self.active[site]['batch'] == parent

    def apply_consignment(self, body, revision):
        located, lines = [], {}
        for item in scan(body['csv']):
            line, record = item['line'], item.get('record')
            if record is None:
                lines[line] = {'site': None, 'status': 'quarantined', 'reason': item['fault']}
                continue
            site = normalize(body['frame'], record['x'], record['y'])
            if site in self.reserved or site not in self.physical:
                lines[line] = {'site': None, 'status': 'quarantined', 'reason': f'格点 {site} 被保留或不属于晶圆'}
            else:
                located.append((line, site, record))
                lines[line] = {'site': list(site)}
        count = Counter(site for _, site, _ in located)
        accepted = []
        for line, site, record in located:
            if count[site] > 1:
                lines[line].update(status='quarantined', reason=f'同一来件多行落到格点 {site}')
            elif not self.reviewable(site, record['parent']):
                lines[line].update(status='quarantined', reason='未引用当前待复测父批次')
            else:
                result = grade(self.spec['rule'], record, is_edge(site, self.physical), True)
                accepted.append((site, record, line, result))
                lines[line].update(status='accepted', state=result['state'])
        for site, record, line, result in accepted:
            self.record_measurement(site, record, result, revision, body['id'], body['frame'], 'repeat', {'line': line})
        self.consignments[body['id']] = {'revision': revision, 'frame': body['frame'], 'lines': lines}
        return lines

    def apply_rework(self, body, revision):
        source = self.consignments.get(body['source'])
        if source is None:
            raise DomainError('source_missing', f'找不到原来件 {body["source"]}', 404)
        lines = source['lines']
        seen = set()
        for item in body['lines']:
            line = item['line']
            if line in seen:
                raise DomainError('line_repeat', f'第 {line} 行重复处置')
            seen.add(line)
            state = lines.get(line)
            if state is None:
                raise DomainError('line_missing', f'原来件 {body["source"]} 没有第 {line} 行')
            if state['status'] != 'quarantined':
                raise DomainError('line_closed', f'第 {line} 行已处置，不能改写', 409)
        mapped = []
        for item in body['lines']:
            if item['action'] != 'correct':
                continue
            record = item['record']
            site = normalize(source['frame'], record['x'], record['y'])
            if site in self.reserved or site not in self.physical:
                raise DomainError('mapped_site', f'格点 {site} 被保留或不属于晶圆')
            if any(site == other for _, other, _ in mapped):
                raise DomainError('mapped_site', f'多行更正落到格点 {site}')
            if not self.reviewable(site, record['parent']):
                raise DomainError('repeat_parent', f'格点 {site} 的父批次不是当前待复测证据', 409)
            mapped.append((item['line'], site, record))
        revoked, corrected = [], []
        for item in body['lines']:
            if item['action'] == 'revoke':
                lines[item['line']].update(status='revoked', by=body['id'])
                revoked.append(item['line'])
        for line, site, record in mapped:
            result = grade(self.spec['rule'], record, is_edge(site, self.physical), True)
            self.record_measurement(site, record, result, revision, body['id'], source['frame'], 'repeat',
                                    {'line': line, 'source': body['source']})
            lines[line].update(status='corrected', by=body['id'], state=result['state'])
            corrected.append({'line': line, 'site': list(site), 'state': result['state']})
        return {'revoked': revoked, 'corrected': corrected}

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
