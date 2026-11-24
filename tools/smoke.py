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
DUAL_SHAPE = '/api/diegrade/wafers/DX-31'
TWIN_SHAPE = '/api/diegrade/wafers/DX-32'
FRAME_A = {'offset': [0, 0], 'turn': 1, 'mirror': False}
FRAME_B = {'offset': [0, 0], 'turn': 0, 'mirror': False}


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

    def batch(self, ID, records, kind='screen', frame=None, expected=None, dry=False, error=None, cond=None):
        batch = {'id': ID, 'kind': kind, 'frame': frame or {'offset': [0, 0], 'turn': 0, 'mirror': False}, 'csv': HEADER + records}
        if cond:
            batch['condition'] = cond
        body = {'expected': self.rev if expected is None else expected, 'batch': batch}
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


def dual_batch(s, shape, rev, ID, records, kind='screen', cond=None, frame=None, dry=False, error=None):
    batch = {'id': ID, 'kind': kind, 'frame': frame or FRAME_B, 'csv': HEADER + records}
    if cond:
        batch['condition'] = cond
    return s.request(shape + ('/preview' if dry else '/batches'), {'expected': rev, 'batch': batch}, error)


def dual(s):
    # 单行掩模下 (-1,0) 是边缘格点；登记锁定两种量测条件与裕量差上限 10 mV。
    spec = {'id': 'DX-31', 'spans': [[0, -1, 1]], 'reserved': [], 'rule': 'sable', 'rule_version': 1,
            'conditions': ['A', 'B'], 'margin_gap': 10}
    assert s.request('/api/diegrade/wafers', spec)['margin_gap'] == 10
    assert s.request('/api/diegrade/wafers', spec)  # 同内容重复登记幂等
    s.request('/api/diegrade/wafers', {**spec, 'margin_gap': 12}, (409, 'wafer_conflict'))
    s.request('/api/diegrade/wafers', {**spec, 'id': 'DX-XX', 'conditions': ['A', 'A']}, (422, 'conditions'))
    # 双条件晶圆的批次必须指定量测条件；单条件晶圆不接受量测条件。
    dual_batch(s, DUAL_SHAPE, 0, 'A00', '0,0,ok,60,95,\n', error=(422, 'condition'))
    s.batch('D01', '0,0,ok,90,55,C01\n', 'repeat', expected=4, dry=True, cond='A', error=(422, 'condition'))
    # 工位 A 首测：设备坐标 (0,1) 逆时针转 90° 归一到物理格点 (-1,0)，开路进入待复测。
    out = dual_batch(s, DUAL_SHAPE, 0, 'A01', '0,1,open,,,\n0,0,ok,60,95,\n0,-1,ok,55,80,\n', cond='A', frame=FRAME_A, dry=True)
    assert len(out['changes']) == 3 and out['revision'] == 0
    dual_batch(s, DUAL_SHAPE, 0, 'A01', '0,1,open,,,\n0,0,ok,60,95,\n0,-1,ok,55,80,\n', cond='A', frame=FRAME_A)
    # 工位 B 直接报 (-1,0)：90 mV 按边缘规则（甲档下限 80+10）得甲。
    dual_batch(s, DUAL_SHAPE, 1, 'B01', '-1,0,ok,50,90,\n0,0,ok,60,100,\n1,0,ok,55,85,\n', cond='B')
    # 两份记录同时保留，但 A 边待复测使这颗仍阻签；格点明细能识别阻签的是条件 A。
    cell = s.request(DUAL_SHAPE + '/cells/-1/0')
    assert cell['state'] == 'review' and len(cell['chain']) == 2
    assert cell['conditions']['A']['state'] == 'review' and cell['conditions']['B']['state'] == 'a'
    view = s.request(DUAL_SHAPE)
    assert view['counts']['review'] == 1 and not view['ready']
    s.request(DUAL_SHAPE + '/releases', {'expected': 2}, (409, 'not_ready'))
    # 用 B 的批次给 A 补测须拒绝；同条件已有记录也不许首测覆盖。
    dual_batch(s, DUAL_SHAPE, 2, 'C01', '0,1,ok,50,70,B01\n', 'repeat', cond='A', frame=FRAME_A, error=(409, 'repeat_parent'))
    dual_batch(s, DUAL_SHAPE, 2, 'B02', '-1,0,ok,50,88,\n', cond='B', error=(409, 'screen_exists'))
    # 改用 A 自己当前的父批次补测 75 mV，按边缘规则单独得乙。
    dual_batch(s, DUAL_SHAPE, 2, 'A02', '0,1,ok,50,75,A01\n', 'repeat', cond='A', frame=FRAME_A)
    cell = s.request(DUAL_SHAPE + '/cells/-1/0')
    assert cell['conditions']['A']['state'] == 'b' and cell['conditions']['A']['margin_mv'] == 75
    # 上限 10 mV：|75-90|=15 严格超过，整颗判退。
    assert cell['state'] == 'reject'
    release = s.request(DUAL_SHAPE + '/releases', {'expected': 3})
    rows = list(csv.DictReader(io.StringIO(s.request(DUAL_SHAPE + '/releases/3/sorter').decode())))
    assert rows[0] == {'x': '-1', 'y': '0', 'bin': 'reject', 'batch_A': 'A02', 'batch_B': 'B01',
                       'margin_A': '75', 'margin_B': '90'}
    # 证据包含两条有效量测、各自来历与登记规则，接收方可独立复算。
    manifest = json.loads(s.request(DUAL_SHAPE + '/releases/3/manifest'))
    payload = manifest['payload']
    assert payload['format'] == 'diegrade.release.2' and payload['wafer']['margin_gap'] == 10
    row = next(r for r in payload['rows'] if [r['x'], r['y']] == [-1, 0])
    assert row['batch'] == {'A': 'A02', 'B': 'B01'} and row['margin'] == {'A': 75, 'B': 90}
    assert s.request(DUAL_SHAPE + '/releases/3/replay')['verified']
    # 已终判的条件经有编号及事由的复核重开，另一条件不受影响。
    s.request(DUAL_SHAPE + '/holds', {'expected': 3, 'id': 'H01', 'site': [0, 0], 'reason': '复核查'}, (422, 'fields'))
    s.request(DUAL_SHAPE + '/holds', {'expected': 3, 'id': 'H01', 'site': [0, 0], 'condition': 'C', 'reason': '复核查'}, (422, 'condition'))
    assert s.request(DUAL_SHAPE + '/holds', {'expected': 3, 'id': 'H01', 'site': [0, 0], 'condition': 'B', 'reason': '复核查'})['revision'] == 4
    cell = s.request(DUAL_SHAPE + '/cells/0/0')
    assert cell['state'] == 'review' and cell['conditions']['A']['state'] == 'a'
    assert cell['conditions']['B']['state'] == 'review'
    s.request(DUAL_SHAPE + '/releases', {'expected': 4}, (409, 'not_ready'))
    # B 条件复测后恢复，签出新版；版差只体现来源变化。
    dual_batch(s, DUAL_SHAPE, 4, 'B03', '0,0,ok,60,98,B01\n', 'repeat', cond='B')
    s.request(DUAL_SHAPE + '/releases', {'expected': 5})
    changes = s.request(DUAL_SHAPE + '/compare/3/5')['changes']
    assert changes == [{'site': [0, 0], 'before': 'a', 'after': 'a', 'kind': 'evidence',
                        'from_batch': {'A': 'A01', 'B': 'B01'}, 'to_batch': {'A': 'A01', 'B': 'B03'}}]
    assert release['sha256'] == s.request(DUAL_SHAPE + '/releases/3/replay')['sha256']


def twin(s):
    # 另一片登记上限 15 mV，其他输入相同：15 不严格超过，非双甲得乙。
    spec = {'id': 'DX-32', 'spans': [[0, -1, 1]], 'reserved': [], 'rule': 'sable', 'rule_version': 1,
            'conditions': ['A', 'B'], 'margin_gap': 15}
    s.request('/api/diegrade/wafers', spec)
    dual_batch(s, TWIN_SHAPE, 0, 'A01', '0,1,open,,,\n0,0,ok,60,95,\n0,-1,ok,55,80,\n', cond='A', frame=FRAME_A)
    dual_batch(s, TWIN_SHAPE, 1, 'B01', '-1,0,ok,50,90,\n0,0,ok,60,100,\n1,0,ok,55,85,\n', cond='B')
    dual_batch(s, TWIN_SHAPE, 2, 'A02', '0,1,ok,50,75,A01\n', 'repeat', cond='A', frame=FRAME_A)
    assert s.request(TWIN_SHAPE + '/cells/-1/0')['state'] == 'b'
    s.request(TWIN_SHAPE + '/releases', {'expected': 3})
    rows = list(csv.DictReader(io.StringIO(s.request(TWIN_SHAPE + '/releases/3/sorter').decode())))
    assert rows[0]['bin'] == 'b' and s.request(TWIN_SHAPE + '/releases/3/replay')['verified']


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare, dual, twin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', choices=[flow.__name__ for flow in STEPS], default='twin')
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
