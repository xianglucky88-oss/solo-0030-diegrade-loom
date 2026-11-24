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

    def consign(self, ID, csv, dry=False, error=None, expected=None):
        body = {'expected': self.rev if expected is None else expected, 'consignment': {
            'id': ID, 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False}, 'csv': csv}}
        return self.wafer('/consignments/preview' if dry else '/consignments', body, error)

    def rework(self, ID, source, lines, error=None, expected=None):
        body = {'expected': self.rev if expected is None else expected,
                'rework': {'id': ID, 'source': source, 'lines': lines}}
        return self.wafer('/reworks', body, error)


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
    s.rev = 4
    s.wafer('/releases', {'expected': 4})
    changes = s.wafer('/compare/2/4')['changes']
    assert changes == [{'site': [0, 0], 'before': 'a', 'after': 'b', 'kind': 'grade', 'from_batch': 'A01', 'to_batch': 'C01'}]
    s.wafer('/compare/4/2', error=(422, 'compare_order'))
    assert s.wafer('/releases/2/sorter') == s.old_bytes


def consign(s):
    # 复核重开 P=(0,-1) 与 Q=(-1,-1)，二者当前待复测父批次都是 A01（即 S）
    for site, tag in (([0, -1], 'H02'), ([-1, -1], 'H03')):
        s.wafer('/holds', {'expected': s.rev, 'site': site, 'id': tag, 'reason': '复测前复核'})
        s.rev += 1
    # 来件 R01：行 2 落 P 且引用 A01；行 3、4 同落 Q，行 4 引用错父批次
    s.r01 = HEADER + '0,-1,ok,80,55,A01\n-1,-1,ok,80,60,A01\n-1,-1,ok,80,61,WRONG\n'
    out = s.consign('R01', s.r01, dry=True)
    assert [c['line'] for c in out['accepted']] == [2]
    assert [c['line'] for c in out['quarantined']] == [3, 4]
    assert out['revision'] == s.rev and s.wafer()['revision'] == s.rev  # 预检不落库
    # 无法定位的行单独隔离，其余行照常接纳
    out = s.consign('RX', HEADER + '0,-1,ok,80,55,A01\n9,9,ok,80,55,A01\n', dry=True)
    assert [c['line'] for c in out['accepted']] == [2] and [c['line'] for c in out['quarantined']] == [3]
    # 整张表无法辨识原始行时整件拒绝
    s.consign('RBAD', 'not,a,valid,header\n1,2,3,4,5,6\n', dry=True, error=(422, 'csv_header'))
    # 正式送交：只让 P 生效
    out = s.consign('R01', s.r01)
    assert out['revision'] == s.rev + 1 and [c['line'] for c in out['accepted']] == [2]
    s.rev += 1
    cell = s.wafer('/cells/0/-1')
    assert cell['state'] == 'b' and cell['chain'][-1]['batch'] == 'R01' and cell['chain'][-1]['line'] == 2
    # Q 仍待复测：隔离行不能解除阻签
    assert s.wafer()['counts'].get('review', 0) == 1
    s.wafer('/releases', {'expected': s.rev}, (409, 'not_ready'))
    # 原样重送不重复量测；同编号改内容须拒绝
    assert s.consign('R01', s.r01)['duplicate'] and s.wafer()['revision'] == s.rev
    s.consign('R01', HEADER + '0,-1,ok,80,56,A01\n', error=(409, 'consignment_conflict'))


def rework(s):
    # 另一来件 R02 合法补测 Q
    out = s.consign('R02', HEADER + '-1,-1,ok,80,60,A01\n')
    assert [c['line'] for c in out['accepted']] == [2] and out['revision'] == s.rev + 1
    s.rev += 1
    assert s.wafer()['counts'].get('review', 0) == 0
    # 复核重开 Q：当前待复测父批次变为 R02
    s.wafer('/holds', {'expected': s.rev, 'site': [-1, -1], 'id': 'H04', 'reason': '复核重开'})
    s.rev += 1
    # 撤销行 4；行 3 仍隔离，不会自动生效
    out = s.rework('W01', 'R01', [{'line': 4, 'action': 'revoke'}])
    assert out['revoked'] == [4] and out['revision'] == s.rev + 1
    s.rev += 1
    assert s.wafer()['counts'].get('review', 0) == 1
    # 已接纳的 P 行不得随之改写
    s.rework('WX', 'R01', [{'line': 2, 'action': 'revoke'}], error=(409, 'line_closed'))
    # 撤销行 4 后行 3 也不能继续用 S（A01）：当前父批次已是 R02
    record = {'x': -1, 'y': -1, 'contact': 'ok', 'leak_na': 80, 'margin_mv': 65, 'parent': 'A01'}
    s.rework('W02', 'R01', [{'line': 3, 'action': 'correct', 'record': record}], error=(409, 'repeat_parent'))
    # 须提交以 R02 为父的更正才能补测
    record['parent'] = 'R02'
    out = s.rework('W02', 'R01', [{'line': 3, 'action': 'correct', 'record': record}])
    assert out['corrected'] == [{'line': 3, 'site': [-1, -1], 'state': 'b'}] and out['revision'] == s.rev + 1
    s.rev += 1
    # 已生效的更正原样重送幂等；同号改内容拒绝；已处置行不可再处置；找不到原来件
    assert s.rework('W02', 'R01', [{'line': 3, 'action': 'correct', 'record': record}])['duplicate']
    s.rework('W02', 'R01', [{'line': 3, 'action': 'revoke'}], error=(409, 'rework_conflict'))
    s.rework('W03', 'R01', [{'line': 3, 'action': 'revoke'}], error=(409, 'line_closed'))
    s.rework('W04', 'NOPE', [{'line': 2, 'action': 'revoke'}], error=(404, 'source_missing'))


def consign_seal(s):
    assert s.wafer()['ready']
    s.wafer('/releases', {'expected': s.rev})
    result = s.wafer(f'/releases/{s.rev}/replay')
    assert result['verified'] and result['rows'] == 8
    # 证据包保留来件原文与更正，接收方可按各次提交当时的现场从原来件复算
    manifest = json.loads(s.wafer(f'/releases/{s.rev}/manifest'))
    events = {e['body']['id']: e for e in manifest['payload']['events'] if e['kind'] in ('consignment', 'rework')}
    assert '0,-1,ok,80,55,A01' in events['R01']['body']['csv'] and 'WRONG' in events['R01']['body']['csv']
    assert events['W02']['body']['lines'][0]['record']['parent'] == 'R02'
    # 核实最终分选由哪些行产生：P ← 来件 R01 第 2 行，Q ← 更正 W02
    rows = {(r['x'], r['y']): r for r in csv.DictReader(io.StringIO(s.wafer(f'/releases/{s.rev}/sorter').decode()))}
    assert rows[('0', '-1')]['batch'] == 'R01' and rows[('-1', '-1')]['batch'] == 'W02'
    # 来件处置一览：行 2 接纳、行 3 已更正、行 4 已撤销
    listing = {c['id']: c for c in s.wafer()['consignments']}
    status = {row['line']: row['status'] for row in listing['R01']['lines']}
    assert status == {2: 'accepted', 3: 'corrected', 4: 'revoked'}
    # 旧通道的签件照常下载和复算
    assert s.wafer('/releases/2/sorter') == s.old_bytes
    assert s.wafer('/releases/2/replay')['verified']


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare, consign, rework, consign_seal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=[flow.__name__ for flow in STEPS], default='consign_seal')
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
