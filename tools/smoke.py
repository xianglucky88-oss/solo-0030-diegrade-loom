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
TRACE_HEADER = 'x,y,contact,leak_na,margin_mv,parent,card,tip,segment\n'
DEMO = Path(__file__).resolve().parent.parent / 'examples'
SHAPE = '/api/diegrade/wafers/LX-19'
MASK = [[-1, -2, 2], [0, -2, 2], [1, -2, 2]]
SITES = [(x, y) for y in (-1, 0, 1) for x in (-2, -1, 0, 1, 2)]


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


def device(turn, px, py):
    x, y = px, py
    for _ in range((4 - turn) % 4):
        x, y = -y, x
    return x, y


def screen_rows(turn, target=None, opens=()):
    lines = []
    for index, (px, py) in enumerate(SITES):
        dx, dy = device(turn, px, py)
        if (px, py) in opens:
            lines.append(f'{dx},{dy},open,,,,PC-118,12,S03')
        elif (px, py) == target:
            lines.append(f'{dx},{dy},ok,50,95,,PC-118,12,S03')
        else:
            lines.append(f'{dx},{dy},ok,50,95,,PC-118,{101 + index},S03')
    return '\n'.join(lines) + '\n'


def post_wafer(s, wid, suffix, body=None, error=None):
    return s.request(f'/api/diegrade/wafers/{wid}{suffix}', body, error)


def batch9(s, wid, rev, bid, rows, kind='screen', turn=0, dry=False, error=None):
    body = {'expected': rev, 'batch': {'id': bid, 'kind': kind,
                                       'frame': {'offset': [0, 0], 'turn': turn, 'mirror': False},
                                       'csv': TRACE_HEADER + rows}}
    return post_wafer(s, wid, '/preview' if dry else '/batches', body, error)


def suspend_setup(s):
    for wid in ('SQ-01', 'SQ-02', 'SQ-03'):
        s.request('/api/diegrade/wafers', {'id': wid, 'spans': MASK, 'reserved': [],
                                           'rule': 'sable', 'rule_version': 1})
    # 同一设备坐标 (0,2)：SQ-01 转 90° 映到西侧 (-2,0)，SQ-02 转 270° 映到东侧 (2,0)，均用同一段触点终档。
    batch9(s, 'SQ-01', 0, 'Q01', screen_rows(1, target=(-2, 0)), turn=1)
    batch9(s, 'SQ-02', 0, 'Q11', screen_rows(3, target=(2, 0)), turn=3)
    # SQ-03 曾用该段触点开路，随后以换针后的同号触点（新工作段 S04）复测终档。
    batch9(s, 'SQ-03', 0, 'Q21', screen_rows(0, opens={(0, 0)}))
    batch9(s, 'SQ-03', 1, 'Q22', '0,0,ok,50,95,Q21,PC-118,12,S04\n', kind='repeat')
    assert post_wafer(s, 'SQ-01', '/cells/-2/0')['state'] == 'a'
    assert post_wafer(s, 'SQ-02', '/cells/2/0')['state'] == 'a'
    assert post_wafer(s, 'SQ-03', '/cells/0/0')['state'] == 'a'
    s.sealed, s.sorters = {}, {}
    for wid, rev in (('SQ-01', 1), ('SQ-02', 1), ('SQ-03', 2)):
        s.sealed[wid] = post_wafer(s, wid, '/releases', {'expected': rev})
        s.sorters[wid] = post_wafer(s, wid, f'/releases/{rev}/sorter')


def suspend_confirm(s):
    payload = {'id': 'SUS-01', 'card': 'PC-118', 'tip': 12, 'segment': 'S03', 'reason': '触点氧化，读数漂移'}
    outcome = s.request('/api/diegrade/suspensions', payload)
    assert not outcome['duplicate']
    assert outcome['affected'] == [{'wafer': 'SQ-01', 'revision': 2, 'sites': [[-2, 0]]},
                                   {'wafer': 'SQ-02', 'revision': 2, 'sites': [[2, 0]]}]
    # 第三片保持当前终档及代次。
    view = post_wafer(s, 'SQ-03', '')
    assert view['revision'] == 2 and view['counts'] == {'a': 15}
    # 重送同一停用确认只生效一次。
    again = s.request('/api/diegrade/suspensions', payload)
    assert again['duplicate'] and again['affected'] == outcome['affected']
    assert post_wafer(s, 'SQ-01', '')['revision'] == 2
    s.request('/api/diegrade/suspensions', {**payload, 'reason': '另一份依据'}, (409, 'suspend_conflict'))
    # 格点追溯显示卡号、触点号、工作段与原始行。
    cell = post_wafer(s, 'SQ-01', '/cells/-2/0')
    assert cell['state'] == 'review' and len(cell['chain']) == 2
    measure, entry = cell['chain']
    assert (measure['record']['card'], measure['record']['tip'], measure['record']['segment']) == ('PC-118', 12, 'S03')
    assert measure['record']['raw'] == '0,2,ok,50,95,,PC-118,12,S03'
    assert entry['event'] == 'suspend' and entry['id'] == 'SUS-01' and entry['batch'] == 'Q01'
    assert (entry['card'], entry['tip'], entry['segment']) == ('PC-118', 12, 'S03') and entry['why'] == payload['reason']
    assert [item['id'] for item in s.request('/api/diegrade/suspensions')] == ['SUS-01']


def suspend_blocks(s):
    # 停用前封存的分选件按原摘要独立复算。
    for wid, rev in (('SQ-01', 1), ('SQ-02', 1), ('SQ-03', 2)):
        result = post_wafer(s, wid, f'/releases/{rev}/replay')
        assert result['verified'] and result['sha256'] == s.sealed[wid]['sha256']
        assert post_wafer(s, wid, f'/releases/{rev}/sorter') == s.sorters[wid]
    # 停用后的正式签出随新现场状态阻签；未受影响晶圆维持原代次。
    post_wafer(s, 'SQ-01', '/releases', {'expected': 2}, (409, 'not_ready'))
    post_wafer(s, 'SQ-02', '/releases', {'expected': 2}, (409, 'not_ready'))
    assert post_wafer(s, 'SQ-03', '/releases', {'expected': 2}) == s.sealed['SQ-03']
    # 预检与正式导入拒收带该组合的新量测，即使复测父批次有效。
    retired = '0,2,ok,50,90,Q01,PC-118,12,S03\n'
    batch9(s, 'SQ-01', 2, 'Q02', retired, kind='repeat', turn=1, dry=True, error=(422, 'suspended'))
    batch9(s, 'SQ-01', 2, 'Q02', retired, kind='repeat', turn=1, error=(422, 'suspended'))
    batch9(s, 'SQ-03', 2, 'Q23', '1,1,ok,50,90,,PC-118,12,S03\n', dry=True, error=(422, 'suspended'))
    # 旧格式来件不携带组合，仍按既有契约工作；预检不推进代次。
    legacy = post_wafer(s, 'SQ-01', '/preview', {'expected': 2, 'batch': {'id': 'Q0L', 'kind': 'repeat',
                        'frame': {'offset': [0, 0], 'turn': 1, 'mirror': False}, 'csv': HEADER + '0,2,ok,50,90,Q01\n'}})
    assert len(legacy['changes']) == 1 and post_wafer(s, 'SQ-01', '')['revision'] == 2


def suspend_retake(s):
    # 换针后同卡同触点在新工作段复测，终判并重新签出。
    outcome = batch9(s, 'SQ-01', 2, 'Q02', '0,2,ok,50,90,Q01,PC-118,12,S04\n', kind='repeat', turn=1)
    assert outcome['revision'] == 3 and outcome['changes'] == [{'site': [-2, 0], 'state': 'a'}]
    cell = post_wafer(s, 'SQ-01', '/cells/-2/0')
    assert cell['state'] == 'a' and cell['batch'] == 'Q02'
    assert batch9(s, 'SQ-01', 3, 'Q02', '0,2,ok,50,90,Q01,PC-118,12,S04\n', kind='repeat', turn=1)['duplicate']
    release = post_wafer(s, 'SQ-01', '/releases', {'expected': 3})
    assert release['revision'] == 3
    replay = post_wafer(s, 'SQ-01', '/releases/3/replay')
    assert replay['verified'] and replay['rows'] == 15
    changes = post_wafer(s, 'SQ-01', '/compare/1/3')['changes']
    assert changes == [{'site': [-2, 0], 'before': 'a', 'after': 'a', 'kind': 'evidence',
                        'from_batch': 'Q01', 'to_batch': 'Q02'}]
    # 第二片仍待复测。
    assert post_wafer(s, 'SQ-02', '/cells/2/0')['state'] == 'review'


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare,
         suspend_setup, suspend_confirm, suspend_blocks, suspend_retake)


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
