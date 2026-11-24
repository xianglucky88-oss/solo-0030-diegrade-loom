"""以临时库演练本地工作站的 HTTP 生产流程。"""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from diegrade.database import Database
from diegrade.httpd import make_server

HEADER = 'x,y,contact,leak_na,margin_mv,parent\n'
DEMO = Path(__file__).resolve().parent.parent / 'examples'
SHAPE = '/api/diegrade/wafers/LX-19'


class Station:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.rev = 0

    def request(self, path, body=None, error=None):
        headers = {'Content-Type': 'application/json'}
        data = None if body is None else json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + path, data, headers), timeout=10) as reply:
                raw, mime = reply.read(), reply.headers.get_content_type()
        except HTTPError as exc:
            if error is None:
                raise RuntimeError(f'{path}: {exc.read().decode()}') from exc
            box = json.load(exc)
            assert (exc.code, box['error']) == error, box
            return box
        assert error is None, f'{path} should fail with {error}'
        return json.loads(raw) if mime == 'application/json' and not path.endswith('/manifest') else raw

    def wafer(self, suffix='', body=None, error=None):
        return self.request(SHAPE + suffix, body, error)

    def batch(self, ID, records, kind='screen', frame=None, expected=None, dry=False, error=None):
        body = {'expected': self.rev if expected is None else expected, 'batch': {
            'id': ID, 'kind': kind, 'frame': frame or {'offset': [0, 0], 'turn': 0, 'mirror': False}, 'csv': HEADER + records}}
        return self.wafer('/preview' if dry else '/batches', body, error)


def rules(s):
    s.rule = json.loads((DEMO / 'sample.json').read_bytes())['rule']
    route = '/api/diegrade/rules'
    assert s.request(route, s.rule) == s.rule
    assert s.request(route, s.rule) == s.rule
    s.request(route, {**s.rule, 'guard': 5}, (409, 'rule_conflict'))


def wafer(s):
    s.spec = {'id': 'LX-19', 'spans': [[-1, -1, 1], [0, -1, 1], [1, -1, 1]], 'reserved': [[0, 1]],
              'rule': 'sable', 'rule_version': 1}
    route = '/api/diegrade/wafers'
    s.request(route, {**s.spec, 'spans': [[0, 0, 1], [0, 1, 2]]}, (422, 'mask_overlap'))
    s.request(route, s.spec)
    s.request('/api/diegrade/rules', {**s.rule, 'version': 2, 'grade_a': 90})
    assert s.wafer()['wafer']['rule']['version'] == 1


def preview(s):
    s.initial = '-1,-1,ok,82,94,\n0,-1,ok,100,60,\n1,-1,ok,101,96,\n-1,0,open,,,\n0,0,ok,98,83,\n1,0,ok,90,50,\n-1,1,ok,90,40,\n1,1,ok,89,94,\n'
    output = s.batch('A01', s.initial, dry=True)
    assert len(output['changes']) == 8 and output['revision'] == 0 and s.wafer()['counts']['missing'] == 8
    s.batch('BAD', '0,1,ok,0,100,\n', dry=True, error=(422, 'mapped_site'))


def ingest(s):
    assert s.batch('A01', s.initial)['revision'] == 1
    s.rev = 1
    assert s.batch('A01', '\n'.join(reversed(s.initial.splitlines())) + '\n')['duplicate']
    s.batch('A01', '0,0,ok,0,99,\n', error=(409, 'batch_conflict'))
    s.batch('MIXED', '-1,0,ok,80,95,A01\n1,0,ok,80,95,MISSING\n', 'repeat', error=(409, 'repeat_parent'))
    assert s.wafer()['revision'] == 1 and not s.wafer('/cells/-1/0')['chain'][1:]


def grid(s):
    view = s.wafer()
    assert view['counts'] == {'a': 3, 'b': 1, 'reject': 2, 'reserved': 1, 'review': 2}
    assert not view['ready']
    s.wafer('/releases', {'expected': 1}, (409, 'not_ready'))
    assert b'/js/app.js' in s.request('/')
    assert b'renderMap' in s.request('/js/wafer-map.js')


def trace(s):
    center = s.wafer('/cells/0/0')
    assert not center['edge'] and center['chain'][0]['margin_min'] == 40
    s.wafer('/cells/3/3', error=(404, 'site_missing'))
    # (0,±1) 坐标转 90° 后落到两个待复测点。
    s.frame = {'offset': [0, 0], 'turn': 1, 'mirror': True}
    records = '0,1,ok,80,90,A01\n0,-1,ok,80,51,A01\n'
    s.batch('B01', records, 'repeat', frame=s.frame, expected=0, error=(409, 'stale'))
    with ThreadPoolExecutor(2) as pool:
        writes = [pool.submit(s.batch, tag, records, 'repeat', s.frame, 1) for tag in ('B01', 'B02')]
        wins, stale = [], 0
        for future in writes:
            try:
                wins.append(future.result())
            except RuntimeError as exc:
                assert '"stale"' in str(exc)
                stale += 1
        assert len(wins) == stale == 1
    s.rev = 2
    chain = s.wafer('/cells/-1/0')['chain']
    assert chain[-1]['record']['parent'] == 'A01' and chain[-1]['record']['y'] == 1 and chain[-1]['state'] == 'a'


def seal(s):
    body = {'expected': s.rev}
    s.release = s.wafer('/releases', body)
    assert len(s.release['sha256']) == 64 and s.release == s.wafer('/releases', body)
    s.wafer('/releases', {'expected': 1}, (409, 'stale'))


def replay(s):
    result = s.wafer('/releases/2/replay')
    assert result['verified'] and result['rows'] == 8 and result['sha256'] == s.release['sha256']
    s.wafer('/releases/1/replay', error=(404, 'release_missing'))


def export(s):
    s.old_bytes = s.wafer('/releases/2/sorter')
    rows = list(csv.DictReader(io.StringIO(s.old_bytes.decode())))
    assert len(rows) == 8 and all((r['x'], r['y']) != ('0', '1') for r in rows)
    assert json.loads(s.wafer('/releases/2/manifest'))['sha256'] == s.release['sha256']
    s.wafer('/releases/3/sorter', error=(404, 'release_missing'))


def hold(s):
    s.wafer('/holds', {'expected': 2, 'site': [0, 1], 'id': 'H00', 'reason': '检查'}, (409, 'hold_state'))
    record = {'expected': 2, 'site': [0, 0], 'id': 'H01', 'reason': '复查接触'}
    assert s.wafer('/holds', record)['revision'] == 3
    assert s.wafer('/holds', {**record, 'expected': 3})['duplicate']
    s.wafer('/releases', {'expected': 3}, (409, 'not_ready'))
    assert s.wafer('/cells/0/0')['chain'][-1]['event'] == 'hold'
    assert s.wafer('/releases/2/sorter') == s.old_bytes
    s.rev = 3


def compare(s):
    s.batch('C01', '0,0,ok,91,55,A01\n', 'repeat')
    s.wafer('/releases', {'expected': 4})
    changes = s.wafer('/compare/2/4')['changes']
    assert changes == [{'site': [0, 0], 'before': 'a', 'after': 'b', 'kind': 'grade', 'from_batch': 'A01', 'to_batch': 'C01'}]
    s.wafer('/compare/4/2', error=(422, 'compare_order'))
    assert s.wafer('/releases/2/sorter') == s.old_bytes


ZONE = '/api/diegrade/wafers/ZW-01'
ZONE_HEADER = HEADER
ZONE_RULE = {'name': 'slice', 'version': 1, 'leak_limit': 100, 'margin_min': 50,
             'grade_a': 75, 'edge_add': 10, 'guard': 3}
ZONE_B = [[-1, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]]
TEMPER = [[-1, -1, 1], [0, -1, 1], [1, -1, 1]]


def zone_rule(s):
    s.request('/api/diegrade/rules', ZONE_RULE)
    shape = {'id': 'ZW-01', 'spans': TEMPER, 'reserved': [[0, -1]],
             'rule': 'slice', 'rule_version': 1, 'zones': {'A': [[0, 0]], 'B': ZONE_B}}
    s.request('/api/diegrade/wafers', {**shape, 'id': 'ZW-02', 'zones': {'A': [[0, 0]], 'B': ZONE_B[:-1]}},
              (422, 'zone_coverage'))
    s.request('/api/diegrade/wafers', {**shape, 'id': 'ZW-03', 'zones': {'A': [[0, 0], [1, 0]], 'B': ZONE_B + [[0, 0]]}},
              (422, 'zone_overlap'))
    s.request('/api/diegrade/wafers', {**shape, 'id': 'ZW-04', 'zones': {'A': [[0, -1]], 'B': ZONE_B}},
              (422, 'zone_reserved'))
    s.request('/api/diegrade/wafers', shape)
    listed = s.request('/api/diegrade/wafers/ZW-01/zones')
    assert [(zone['id'], zone['count']) for zone in listed['zones']] == [('A', 1), ('B', 7)]
    assert listed['testable'] == 8
    # 分区不改变边缘判定，保留点仍是物理邻居：中央 P 为内点。
    assert s.request('/api/diegrade/wafers/ZW-01/cells/0/0')['edge'] is False
    assert s.request('/api/diegrade/wafers/ZW-01/cells/1/1')['edge'] is True
    s.request('/api/diegrade/wafers/ZW-01/zone-plans', {'expected': 0, 'zone': 'A'}, (409, 'zone_not_ready'))


def zone_workflow(s):
    base = ZONE
    screen = lambda ident, rows, expected, kind='screen': s.request(base + '/batches', {'expected': expected, 'batch': {
        'id': ident, 'kind': kind, 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': ZONE_HEADER + rows}})
    assert screen('S01', '0,0,ok,10,78,\n', 0)['changes'] == [{'site': [0, 0], 'state': 'a'}]
    plan = s.request(base + '/zone-plans', {'expected': 1, 'zone': 'A'})
    assert plan['seq'] == 1 and plan['sites'] == 1 and not plan['duplicate']
    assert s.request(base + '/zone-plans', {'expected': 1, 'zone': 'A'})['duplicate']
    review = s.request(base + '/zone-plans/A/1')
    assert review['stale'] is False and review['entries'][0]['bin'] == 'a'
    s.request(base + '/releases', {'expected': 1}, (409, 'zoned_wafer'))
    screen('S02', '-1,-1,ok,10,80,\n', 1)
    first = s.request(base + '/zone-releases', {'expected': 2, 'zone': 'A', 'plan': 1})
    assert first['rows'] == 1 and not first['duplicate']
    retried = s.request(base + '/zone-releases', {'expected': 2, 'zone': 'A', 'plan': 1})
    assert retried['duplicate'] and retried['sha256'] == first['sha256']
    assert s.request(base + '/zone-releases/A/sorter') == b'x,y,bin,batch\n0,0,a,S01\n'
    assert json.loads(s.request(base + '/zone-releases/A/manifest'))['payload']['records'][0]['batch'] == 'S01'
    assert s.request(base + '/zone-ledger', error=(409, 'zone_incomplete'))
    rows = ''.join(f'{x},{y},ok,10,80,\n' for x, y in ZONE_B if (x, y) != (-1, -1))
    screen('S03', rows, 2)
    plan_b = s.request(base + '/zone-plans', {'expected': 3, 'zone': 'B'})
    s.request(base + '/zone-releases', {'expected': 3, 'zone': 'B', 'plan': plan_b['seq']})
    ledger = json.loads(s.request(base + '/zone-ledger/manifest'))
    assert s.request(base + '/zone-ledger/verify', ledger)['cells'] == 8
    assert [cell['bin'] for cell in ledger['payload']['cells'] if cell['site'] == [0, 0]] == ['a']
    forged = json.loads(json.dumps(ledger))
    a_cell = next(c for c in forged['payload']['cells'] if c['zone'] == 'A')
    for cell in forged['payload']['cells']:
        if cell['zone'] == 'B':
            cell.update(a_cell)
            break
    from diegrade.codec import digest
    forged['sha256'] = digest(forged['payload'])
    s.request(base + '/zone-ledger/verify', forged, (409, 'ledger_overlap'))
    assert len(forged['payload']['cells']) == len(ledger['payload']['cells'])


def zone_history(s):
    base = ZONE
    plan = s.request(base + '/zone-plans', {'expected': 3, 'zone': 'A'})
    s.request(base + '/holds', {'expected': 3, 'id': 'H1', 'site': [0, 0], 'reason': '复测'})
    s.request(base + '/batches', {'expected': 4, 'batch': {
        'id': 'R01', 'kind': 'repeat', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': ZONE_HEADER + '0,0,ok,10,65,S01\n'}})
    assert s.request(base + '/cells/0/0')['state'] == 'b'
    s.rev = 5
    assert s.request(base + '/zone-releases/A/sorter') == b'x,y,bin,batch\n0,0,a,S01\n'
    fresh = s.request(base + '/zone-plans', {'expected': 5, 'zone': 'A'})
    assert fresh['seq'] == 2 and not fresh['duplicate']
    s.request(base + '/zone-releases', {'expected': 5, 'zone': 'A', 'plan': fresh['seq']}, (409, 'zone_released'))
    assert s.request(base + '/zone-releases/A/sorter') == b'x,y,bin,batch\n0,0,a,S01\n'
    assert s.request(base + '/zone-plans/A/2')['entries'][0]['bin'] == 'b'
    assert plan['seq'] == 1


def zone_source_swap(s):
    """同档换源：旧方案失效，必须重新准备。"""
    base = '/api/diegrade/wafers/ZW-11'
    request = s.request
    request('/api/diegrade/wafers', {'id': 'ZW-11', 'spans': TEMPER, 'reserved': [[0, -1]],
                                     'rule': 'slice', 'rule_version': 1, 'zones': {'A': [[0, 0]], 'B': ZONE_B}})
    request(base + '/batches', {'expected': 0, 'batch': {
        'id': 'S01', 'kind': 'screen', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': ZONE_HEADER + '0,0,ok,10,78,\n'}})
    plan = request(base + '/zone-plans', {'expected': 1, 'zone': 'A'})
    request(base + '/holds', {'expected': 1, 'id': 'H1', 'site': [0, 0], 'reason': '复核'})
    request(base + '/batches', {'expected': 2, 'batch': {
        'id': 'R01', 'kind': 'repeat', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': ZONE_HEADER + '0,0,ok,10,76,S01\n'}})
    assert request(base + '/cells/0/0')['state'] == 'a'
    assert request(base + '/zone-plans/A/1')['stale'] is True
    request(base + '/zone-releases', {'expected': 3, 'zone': 'A', 'plan': plan['seq']}, (409, 'plan_stale'))
    fresh = request(base + '/zone-plans', {'expected': 3, 'zone': 'A'})
    assert fresh['seq'] == 2 and not fresh['duplicate']
    assert request(base + '/zone-releases', {'expected': 3, 'zone': 'A', 'plan': 2})['rows'] == 1
    assert request(base + '/zone-releases/A/sorter') == b'x,y,bin,batch\n0,0,a,R01\n'


def zone_ledger_forged(s):
    """清册总行数不变，却把 B 的一个格点换成重复的 A 格点：复算必须拒绝。"""
    base = ZONE
    ledger = json.loads(s.request(base + '/zone-ledger/manifest'))
    forged = json.loads(json.dumps(ledger))
    a_cell = next(cell for cell in forged['payload']['cells'] if cell['zone'] == 'A')
    for cell in forged['payload']['cells']:
        if cell['zone'] == 'B':
            cell.update(a_cell)
            break
    from diegrade.codec import digest
    forged['sha256'] = digest(forged['payload'])
    assert len(forged['payload']['cells']) == len(ledger['payload']['cells'])
    s.request(base + '/zone-ledger/verify', forged, (409, 'ledger_overlap'))
    # 随附正式件本身也被改写时，即便重算外层摘要也拒绝。
    tampered = json.loads(json.dumps(ledger))
    b_piece = next(piece for piece in tampered['payload']['zones'] if piece['zone'] == 'B')
    b_piece['release']['records'][0]['bin'] = 'a'
    tampered['sha256'] = digest(tampered['payload'])
    s.request(base + '/zone-ledger/verify', tampered, (409, 'zone_release_digest'))


def zone_legacy(s):
    """未启用分区的晶圆照常整片签出和复算。"""
    s.request('/api/diegrade/wafers', {'id': 'WZ-01', 'spans': [[0, 0, 0]], 'reserved': [],
                                       'rule': 'slice', 'rule_version': 1})
    base = '/api/diegrade/wafers/WZ-01'
    s.request(base + '/batches', {'expected': 0, 'batch': {
        'id': 'S01', 'kind': 'screen', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': ZONE_HEADER + '0,0,ok,10,90,\n'}})
    sealed = s.request(base + '/releases', {'expected': 1})
    assert s.request(base + '/releases/1/replay')['rows'] == 1
    assert s.request(base + '/releases/1/sorter') == b'x,y,bin,batch\n0,0,a,S01\n'
    assert s.request(base + '/zones')['zoned'] is False
    assert sealed['sha256'] == s.request(base + '/releases/1/replay')['sha256']


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare,
         zone_rule, zone_workflow, zone_history, zone_source_swap, zone_ledger_forged, zone_legacy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=[flow.__name__ for flow in STEPS], default=STEPS[-1].__name__)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='diegrade-smoke-') as temp, make_server(Database(Path(temp) / 'db'), 0) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = Station(server.server_port)
            for flow in STEPS:
                flow(client)
                print(f'通过：{flow.__name__}')
                if flow.__name__ == args.through:
                    break
        finally:
            server.shutdown()
            worker.join()


if __name__ == '__main__':
    main()
