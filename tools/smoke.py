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
CYCLE = '/api/diegrade/wafers/CX-03'


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
    assert result['batches'][0]['sites']


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


def cycle(s):
    """3×3 晶圆用对准标记确认位姿后接纳量测；旧批次仍可不用标记。"""
    s.request('/api/diegrade/rules', {'name': 'edgecal', 'version': 1, 'leak_limit': 100,
                                       'margin_min': 40, 'grade_a': 80, 'edge_add': 10, 'guard': 3})
    shape = {'id': 'CX-03', 'spans': [[-1, -1, 1], [0, -1, 1], [1, -1, 1]],
             'reserved': [[0, 0], [0, 1]], 'rule': 'edgecal', 'rule_version': 1}
    s.request('/api/diegrade/wafers', shape)
    s.request(CYCLE + '/cycles', {'expected': 0, 'cycle': {
        'id': 'K1', 'poses': [[0, False], [2, False]], 'markers': {'M0': [0, 0], 'M1': [0, 1]}}})
    # 位姿尚未确定：首测被拒，代次不推进。
    body = {'expected': 1, 'batch': {'id': 'S1', 'kind': 'screen', 'cycle': 'K1',
            'csv': HEADER + '1,0,ok,10,96,\n-1,0,ok,10,75,\n'}}
    s.request(CYCLE + '/batches', body, (409, 'cycle_unconfirmed'))
    assert s.request(CYCLE)['revision'] == 1
    # 中心标记只给出两种可能；重送不增加证据。
    first = s.request(CYCLE + '/cycles/K1/marks', {'expected': 1, 'marker': 'M0', 'device': [0, 0]})
    assert first['state']['state'] == 'undetermined' and first['revision'] == 2
    assert s.request(CYCLE + '/cycles/K1/marks', {'expected': 2, 'marker': 'M0', 'device': [0, 0]})['duplicate']
    # (0,1) 标记从 (0,-1) 报到，唯一合法位姿为 180 度。
    second = s.request(CYCLE + '/cycles/K1/marks', {'expected': 2, 'marker': 'M1', 'device': [0, -1]})
    assert second['newly_locked'] and second['state']['state'] == 'confirmed'
    assert second['state']['pose'] == {'offset': [0, 0], 'turn': 2, 'mirror': False}
    s.request(CYCLE + '/cycles/K1/marks', {'expected': 3, 'marker': 'M1', 'device': [0, 1]},
              (409, 'cycle_observation_conflict'))
    body['expected'] = 3
    body['batch']['id'] = 'S1'
    assert s.request(CYCLE + '/batches', body)['revision'] == 4
    rest = HEADER + '1,1,ok,10,90,\n0,1,ok,10,75,\n-1,1,ok,10,90,\n1,-1,ok,10,75,\n-1,-1,ok,10,90,\n'
    assert s.request(CYCLE + '/batches', {'expected': 4, 'batch': {'id': 'S2', 'kind': 'screen',
              'cycle': 'K1', 'csv': rest}})['revision'] == 5
    s.request(CYCLE + '/batches', {'expected': 5, 'batch': {'id': 'S3', 'kind': 'screen', 'cycle': 'K1',
              'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False}, 'csv': HEADER + '1,0,ok,10,96,\n'}},
              (409, 'cycle_pose_conflict'))
    s.request(CYCLE + '/releases', {'expected': 5})
    replay = s.request(CYCLE + '/releases/5/replay')
    assert replay['verified'] and replay['cycles'][0]['pose']['turn'] == 2
    s.package = json.loads(s.request(CYCLE + '/releases/5/manifest'))
    receipt = s.request('/api/diegrade/verify', {'wafer': 'CX-03', **s.package})
    assert receipt['verified'] and receipt['wafer'] == 'CX-03'
    # 接收方复算应报出各批次采用的位姿与归到的物理点。
    by_id = {item['id']: item for item in receipt['batches']}
    assert by_id['S1']['pose']['turn'] == 2 and [-1, 0] in by_id['S1']['sites'] and [1, 0] in by_id['S1']['sites']
    rows = {(row['x'], row['y']): row['bin'] for row in s.package['payload']['rows']}
    assert rows == {(0, -1): 'b', (1, -1): 'a', (-1, -1): 'a', (-1, 1): 'b', (1, 1): 'a', (-1, 0): 'a', (1, 0): 'b'}, rows
    # 同一份标记证据改成 0 度，重建行与摘要后接收方仍须拒绝。
    from diegrade.codec import digest
    from diegrade.sorter import assignments
    from diegrade.timeline import replay_timeline
    forged = json.loads(json.dumps(s.package))
    forged['payload']['cycles'][0]['pose'] = {'offset': [0, 0], 'turn': 0, 'mirror': False}
    forged['sha256'] = digest(forged['payload'])
    s.request('/api/diegrade/verify', forged, (409, 'release_replay'))
    # 旧批次仍可不选周期、自带坐标系。
    s.request(CYCLE + '/batches', {'expected': 5, 'batch': {
        'id': 'LEGACY', 'kind': 'screen', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
        'csv': HEADER + '1,0,ok,10,96,\n'}}, (409, 'screen_exists'))


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare, cycle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=[flow.__name__ for flow in STEPS], default='cycle')
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
