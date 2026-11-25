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
        self.rounds = {}
        self.open_rounds = {}
        self.batch_ids = set()

    def apply(self, event):
        handlers = {'batch': self.apply_batch, 'hold': self.apply_hold, 'round': self.apply_round,
                    'submission': self.apply_submission, 'verdict': self.apply_verdict, 'void': self.apply_void}
        try:
            handler = handlers[event['kind']]
        except KeyError as exc:
            raise DomainError('event_kind', f'未知的现场记录类型 {event["kind"]}', 409) from exc
        handler(event['body'], event['revision'])

    # 首测、复测与复核。

    def apply_batch(self, batch, revision):
        repeated = batch['kind'] == 'repeat'
        if batch['id'] in self.batch_ids:
            raise DomainError('batch_conflict', f'批次编号 {batch["id"]} 已用于另一份证据', 409)
        decisions = []
        for site, record in map_sites(batch, self.physical, self.reserved):
            older = self.active.get(site)
            if repeated:
                if site in self.open_rounds:
                    raise DomainError('round_open', f'格点 {site} 的复测轮次尚未结案，普通复测不能改档', 409)
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
        self.batch_ids.add(batch['id'])
        return decisions

    def apply_hold(self, body, revision):
        site = tuple(body['site'])
        if self.state(site) not in FINAL:
            raise DomainError('hold_state', '只有已经判档的格点可重新开启复测', 409)
        hold = {'event': 'hold', 'why': body['reason'], 'revision': revision, 'batch': self.active[site]['batch']}
        self.holds[site] = hold
        self.history[site].append(hold)

    # 两站复测轮次。父量测在开轮时锁定为该格点当时的最新有效测量。

    def apply_round(self, body, revision):
        round_id, site = body['id'], tuple(body['site'])
        if round_id in self.rounds:
            raise DomainError('round_conflict', '轮次编号已用于另一条现场记录', 409)
        self.cell(site)
        if self.state(site) != 'review':
            raise DomainError('round_state', '只有当前待复测的格点可开立复测轮次', 409)
        if site in self.open_rounds:
            raise DomainError('round_open', '该格点已有未结的复测轮次', 409)
        if not self.active.get(site):
            raise DomainError('round_parent', '该格点没有可锁定的父量测', 409)
        parent = self.active[site]['batch']
        self.rounds[round_id] = {'body': body, 'revision': revision, 'parent': parent,
                                 'submissions': {}, 'status': 'collecting', 'verdict': None, 'void': None}
        self.open_rounds[site] = round_id
        self.history.setdefault(site, []).append(
            {'event': 'round', 'id': round_id, 'why': '开立两站复测轮次', 'revision': revision,
             'parent': parent, 'stations': list(body['stations'])})

    def apply_submission(self, body, revision):
        round_id, station = body['round'], body['station']
        found = self.rounds.get(round_id)
        if not found:
            raise DomainError('round_missing', '复测轮次不存在', 404)
        if found['status'] not in ('collecting', 'ready'):
            raise DomainError('round_closed', '本轮已结案或作废，不再接收来件', 409)
        if station not in found['body']['stations']:
            raise DomainError('station', '该工位未登记在本轮', 409)
        if station in found['submissions']:
            raise DomainError('submission_conflict', '该工位已送回证据，不能覆盖', 409)
        batch = body['batch']
        if batch['kind'] != 'repeat' or len(batch['rows']) != 1:
            raise DomainError('submission_shape', '每站来件须恰为一颗复测记录', 422)
        if batch['id'] in self.batch_ids:
            raise DomainError('batch_conflict', f'批次编号 {batch["id"]} 已用于另一份证据', 409)
        site, record = map_sites(batch, self.physical, self.reserved)[0]
        if site != tuple(found['body']['site']):
            raise DomainError('submission_site', f'来件未投向本轮指定格点 {tuple(found["body"]["site"])}', 422)
        if record['parent'] != found['parent']:
            raise DomainError('submission_parent', f'来件父批次须为本轮父量测 {found["parent"]}', 409)
        graded = grade(self.spec['rule'], record, is_edge(site, self.physical), True)
        submission = {'event': 'submission', 'round': round_id, 'station': station, 'site': list(site),
                      'parent': found['parent'], 'batch': batch['id'], 'revision': revision,
                      'frame': batch['frame'], 'record': record, **graded}
        found['submissions'][station] = submission
        self.batch_ids.add(batch['id'])
        if len(found['submissions']) == len(found['body']['stations']):
            found['status'] = 'ready'
        self.history.setdefault(site, []).append(submission)

    def apply_verdict(self, body, revision):
        round_id, station = body['round'], body['station']
        found = self.rounds.get(round_id)
        if not found:
            raise DomainError('round_missing', '复测轮次不存在', 404)
        if found['status'] == 'decided':
            raise DomainError('round_closed', '本轮已经裁决', 409)
        if found['status'] != 'ready':
            raise DomainError('round_incomplete', '两站来件尚未收齐，不能裁决', 409)
        if station not in found['submissions']:
            raise DomainError('verdict_station', '所选来件不属于本轮', 409)
        chosen = found['submissions'][station]
        site = tuple(found['body']['site'])
        result = grade(self.spec['rule'], chosen['record'], is_edge(site, self.physical), True)
        if result['state'] not in FINAL:
            raise DomainError('verdict_state', '所选来件为开路，不能终判', 409)
        decision = {**result, 'event': 'verdict', 'batch': chosen['batch'], 'revision': revision,
                    'frame': chosen['frame'], 'kind': 'repeat', 'record': chosen['record'],
                    'round': round_id, 'station': station, 'id': body['id']}
        self.active[site] = decision
        self.holds.pop(site, None)
        self.history[site].append(decision)
        found['status'] = 'decided'
        found['verdict'] = {'id': body['id'], 'station': station, 'reason': body['reason'], 'revision': revision}
        self.open_rounds.pop(site, None)

    def apply_void(self, body, revision):
        round_id = body['round']
        found = self.rounds.get(round_id)
        if not found:
            raise DomainError('round_missing', '复测轮次不存在', 404)
        if found['status'] != 'ready':
            raise DomainError('void_state', '只有两站都送回开路时才能作废本轮', 409)
        if any(item['state'] != 'review' for item in found['submissions'].values()):
            raise DomainError('void_state', '只有两站都送回开路时才能作废本轮', 409)
        site = tuple(found['body']['site'])
        found['status'] = 'void'
        found['void'] = {'id': body['id'], 'reason': body['reason'], 'revision': revision}
        self.open_rounds.pop(site, None)
        self.history.setdefault(site, []).append(
            {'event': 'void', 'id': body['id'], 'why': body['reason'], 'revision': revision, 'round': round_id})

    # 现场状态与对外视图。

    def state(self, site):
        if site in self.reserved:
            return 'reserved'
        if site in self.holds:
            return 'review'
        if site in self.open_rounds:
            return 'review'
        return self.active[site]['state'] if site in self.active else 'missing'

    def grid(self):
        return [self.cell(site) for site in order(self.physical)]

    def cell(self, site):
        if site not in self.physical:
            raise DomainError('site_missing', '格点不属于本片晶圆', 404)
        return {'site': list(site), 'edge': is_edge(site, self.physical), 'state': self.state(site),
                'batch': self.active.get(site, {}).get('batch'), 'round': self.open_rounds.get(site)}

    def round_views(self):
        views = []
        for round_id, found in self.rounds.items():
            stations = found['body']['stations']
            views.append({
                'id': round_id, 'site': list(found['body']['site']), 'parent': found['parent'],
                'stations': list(stations), 'status': found['status'], 'revision': found['revision'],
                'submissions': [self.submission_view(found['submissions'][name])
                                for name in stations if name in found['submissions']],
                'verdict': dict(found['verdict']) if found['verdict'] else None,
                'void': dict(found['void']) if found['void'] else None})
        return views

    @staticmethod
    def submission_view(submission):
        return {key: submission[key] for key in
                ('station', 'batch', 'site', 'parent', 'state', 'why', 'record', 'frame',
                 'margin_min', 'grade_a', 'revision')}


def replay_timeline(spec, events):
    timeline = Timeline(spec)
    for event in events:
        timeline.apply(event)
    return timeline
