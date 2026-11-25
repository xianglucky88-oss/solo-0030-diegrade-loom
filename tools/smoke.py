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
        return json.loads(raw) if mime == 'application/json' and '/manifest' not in path else raw

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


def rounds(s):
    """同一颗芯粒由两个指定工位各复测一次，复核员裁决采用其中一份。"""
    s.rev = 4
    s.wafer('/holds', {'expected': s.rev, 'site': [0, 0], 'id': 'H10', 'reason': '两站复测'})
    s.rev = 5
    opened = s.wafer('/rounds', {'expected': s.rev, 'id': 'R10', 'site': [0, 0],
                                 'stations': ['S1', 'S2'], 'reason': '内部点复测'})
    assert opened == {'revision': 6, 'duplicate': False, 'parent': 'C01'}, opened
    s.rev = 6
    s.wafer('/rounds', {'expected': s.rev, 'id': 'R11', 'site': [0, 0], 'stations': ['S1', 'S2'],
                        'reason': '重叠'}, (409, 'round_open'))
    s.batch('X', '0,0,ok,90,60,C01\n', 'repeat', expected=s.rev, error=(409, 'round_open'))
    sub = '/rounds/R10/submissions'
    frame0 = {'offset': [0, 0], 'turn': 0, 'mirror': False}
    s.wafer(sub, {'expected': s.rev, 'station': 'S1', 'batch': {'id': 'S1A', 'kind': 'repeat',
        'frame': frame0, 'csv': HEADER + '-1,0,ok,90,70,C01\n'}}, (422, 'submission_site'))
    s.wafer(sub, {'expected': s.rev, 'station': 'S1', 'batch': {'id': 'S1A', 'kind': 'repeat',
        'frame': frame0, 'csv': HEADER + '0,0,ok,90,85,C01\n'}})
    s.rev = 7
    s.wafer(sub, {'expected': s.rev, 'station': 'S1', 'batch': {'id': 'S1B', 'kind': 'repeat',
        'frame': frame0, 'csv': HEADER + '0,0,ok,90,84,C01\n'}}, (409, 'submission_conflict'))
    assert s.wafer(sub, {'expected': s.rev, 'station': 'S1', 'batch': {'id': 'S1A', 'kind': 'repeat',
        'frame': frame0, 'csv': HEADER + '0,0,ok,90,85,C01\n'}})['duplicate']
    s.wafer('/releases', {'expected': s.rev}, (409, 'not_ready'))
    s.wafer('/rounds/R10/verdicts', {'expected': s.rev, 'id': 'V10', 'station': 'S1',
                                     'reason': '提前'}, (409, 'round_incomplete'))
    s.wafer(sub, {'expected': s.rev, 'station': 'S2', 'batch': {'id': 'S2A', 'kind': 'repeat',
        'frame': frame0, 'csv': HEADER + '0,0,ok,90,60,C01\n'}})
    s.rev = 8
    s.wafer('/releases', {'expected': s.rev}, (409, 'not_ready'))
    s.wafer('/rounds/R10/verdicts', {'expected': s.rev, 'id': 'V10', 'station': 'S2', 'reason': '采用乙'})
    s.rev = 9
    s.wafer('/rounds/R10/verdicts', {'expected': 8, 'id': 'V11', 'station': 'S1', 'reason': '迟到'}, (409, 'stale'))
    release = s.wafer('/releases', {'expected': s.rev})
    assert len(release['sha256']) == 64
    replay = s.wafer(f'/releases/{s.rev}/replay')
    assert replay['verified'] and replay['rounds'] == 1
    manifest = json.loads(s.wafer(f'/releases/{s.rev}/manifest'))
    found = manifest['payload']['rounds'][0]
    assert found['parent'] == 'C01' and [item['station'] for item in found['submissions']] == ['S1', 'S2']
    rows = list(csv.DictReader(io.StringIO(s.wafer(f'/releases/{s.rev}/sorter').decode())))
    row = next(item for item in rows if (item['x'], item['y']) == ('0', '0'))
    assert (row['bin'], row['batch']) == ('b', 'S2A'), row
    # 以后的新复核、迟到来件与重送都不动已签件。
    signed = s.wafer(f'/releases/{s.rev}/sorter')
    s.wafer('/holds', {'expected': s.rev, 'site': [0, 0], 'id': 'H11', 'reason': '再复核'})
    s.rev = 10
    s.wafer('/rounds/R10/submissions', {'expected': s.rev, 'station': 'S1', 'batch': {
        'id': 'LATE', 'kind': 'repeat', 'frame': frame0, 'csv': HEADER + '0,0,ok,90,95,C01\n'}},
        (409, 'submission_conflict'))
    assert s.wafer(f'/releases/{s.rev - 1}/sorter') == signed
    assert s.wafer(f'/releases/{s.rev - 1}/replay')['verified']


def round_void(s):
    """两站都开路时作废本轮，以原父量测重开；旧编号来件不得占用新轮名额。"""
    frame0 = {'offset': [0, 0], 'turn': 0, 'mirror': False}
    s.wafer('/rounds', {'expected': s.rev, 'id': 'R12', 'site': [0, 0],
                        'stations': ['S1', 'S2'], 'reason': '开路轮'})
    s.rev += 1
    for station, batch_id in (('S1', 'O1'), ('S2', 'O2')):
        s.wafer('/rounds/R12/submissions', {'expected': s.rev, 'station': station,
            'batch': {'id': batch_id, 'kind': 'repeat', 'frame': frame0, 'csv': HEADER + '0,0,open,,,S2A\n'}})
        s.rev += 1
    s.wafer('/releases', {'expected': s.rev}, (409, 'not_ready'))
    s.batch('Y', '0,0,ok,90,60,S2A\n', 'repeat', expected=s.rev, error=(409, 'round_open'))
    s.wafer('/rounds/R12/voids', {'expected': s.rev, 'id': 'W12', 'reason': '两站开路'})
    s.rev += 1
    s.wafer('/rounds', {'expected': s.rev, 'id': 'R13', 'site': [0, 0],
                        'stations': ['S1', 'S2'], 'reason': '重开'})
    s.rev += 1
    # 甲站仍写旧轮编号的另一条有效读数：拒绝，且不占用新一轮名额。
    s.wafer('/rounds/R12/submissions', {'expected': s.rev, 'station': 'S1',
        'batch': {'id': 'LATE', 'kind': 'repeat', 'frame': frame0,
                  'csv': HEADER + '0,0,ok,90,95,S2A\n'}}, (409, 'submission_conflict'))
    assert s.wafer()['revision'] == s.rev
    s.wafer('/rounds/R13/submissions', {'expected': s.rev, 'station': 'S1',
        'batch': {'id': 'N1', 'kind': 'repeat', 'frame': frame0,
                  'csv': HEADER + '0,0,ok,90,95,S2A\n'}})
    s.rev += 1
    view = next(item for item in s.wafer()['rounds'] if item['id'] == 'R12')
    assert view['status'] == 'void' and len(view['submissions']) == 2
    assert all(item['state'] == 'review' for item in view['submissions'])
    assert s.wafer()['counts']['review'] == 1


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare, rounds, round_void)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=[flow.__name__ for flow in STEPS], default='round_void')
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
