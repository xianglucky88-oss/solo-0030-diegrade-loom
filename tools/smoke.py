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
    assert changes == [{'site': [0, 0], 'before': 'a', 'after': 'b', 'kind': 'grade',
                        'from_batch': 'A01', 'to_batch': 'C01', 'from_device': [0, 0], 'to_device': [0, 0]}]
    s.wafer('/compare/4/2', error=(422, 'compare_order'))
    assert s.wafer('/releases/2/sorter') == s.old_bytes


def series(s, wafer_id):
    """在一块新晶圆上操作，代次由现场摘要读取。"""
    base = '/api/diegrade/wafers/' + wafer_id
    state = {'revision': s.request(base)['revision']}

    def view(suffix='', body=None, error=None):
        result = s.request(base + suffix, body, error)
        if body is not None and error is None and 'revision' in result:
            state['revision'] = result['revision']
        return result

    def sub(ID, records, kind='screen', frame=None, error=None):
        body = {'expected': state['revision'], 'batch': {
            'id': ID, 'kind': kind, 'frame': frame or {'offset': [0, 0], 'turn': 0, 'mirror': False},
            'csv': HEADER + records}}
        return view('/batches', body, error)

    def correct(ID, batch, frame, reason='设备位姿录入有误', error=None, expected=None):
        body = {'expected': state['revision'] if expected is None else expected,
                'id': ID, 'batch': batch, 'frame': frame, 'reason': reason}
        return view('/corrections', body, error)

    return view, sub, correct


def correct(s):
    shape = {'id': 'LX-20', 'spans': [[0, -2, 2]], 'reserved': [[0, 0]], 'rule': 'sable', 'rule_version': 1}
    s.request('/api/diegrade/wafers', shape)
    view, sub, fix = series(s, 'LX-20')

    # 首测 S：−2、−1、1、2 的合规裕量为 60、48、50、65；边缘乙档下限 50、甲档 90、复测带宽 3。
    screen = '-2,0,ok,10,60,\n-1,0,ok,10,48,\n1,0,ok,10,50,\n2,0,ok,10,65,\n'
    assert [(c['site'], c['state']) for c in sub('S01', screen)['changes']] == [
        ([-2, 0], 'b'), ([-1, 0], 'review'), ([1, 0], 'review'), ([2, 0], 'b')]
    # 独立工位以 S 为父补到 60 mV，中间两点转为乙档，四颗齐备签出第一版。
    sub('T01', '-1,0,ok,10,60,S01\n1,0,ok,10,60,S01\n', 'repeat')
    assert view()['counts'] == {'b': 4, 'reserved': 1}
    assert view()['ready']
    view('/releases', {'expected': view()['revision']})
    assert view('/releases/2/replay')['rows'] == 4
    first_bytes = view('/releases/2/sorter')
    assert first_bytes.decode().splitlines()[1:] == ['-2,0,b,S01', '-1,0,b,T01', '1,0,b,T01', '2,0,b,S01']

    # 不接受落在保留点、移出掩模、改变原占点集合、空更正或非首测批次的位姿。
    identity_frame = {'offset': [0, 0], 'turn': 0, 'mirror': False}
    fix('C0', 'S01', identity_frame, error=(422, 'correction_noop'))
    fix('C1', 'S01', {'offset': [1, 0], 'turn': 0, 'mirror': False}, error=(422, 'correction_site'))
    fix('C2', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, error=(409, 'stale'), expected=0)
    fix('C3', 'T01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, error=(422, 'correction_kind'))
    fix('C4', 'NOPE', {'offset': [0, 0], 'turn': 0, 'mirror': True}, error=(404, 'correction_batch'))
    assert view()['revision'] == 2 and view()['counts'] == {'b': 4, 'reserved': 1}

    # 不能借位姿把另一区的点拉进来：平移后物理点集合改变即整次拒绝。
    far = {'id': 'LX-21', 'spans': [[0, -3, 3]], 'reserved': [], 'rule': 'sable', 'rule_version': 1}
    s.request('/api/diegrade/wafers', far)
    _, far_sub, far_fix = series(s, 'LX-21')
    far_sub('F01', '-3,0,ok,10,60,\n-2,0,ok,10,60,\n-1,0,ok,10,60,\n0,0,ok,10,60,\n')
    far_fix('F02', 'F01', {'offset': [3, 0], 'turn': 0, 'mirror': False}, error=(422, 'correction_set'))

    # 只更正 S 的横坐标镜像：四行在原占点集合内一一互换。
    out = fix('C1', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, reason='首测设备横轴镜像录反')
    assert out['revision'] == 3
    assert sorted((tuple(c['site']), c['state'], c['reopened']) for c in out['changes']) == [
        ((-2, 0), 'b', False), ((-1, 0), 'review', True), ((1, 0), 'review', True), ((2, 0), 'b', False)]
    view_after = view()
    assert view_after['counts'] == {'b': 2, 'review': 2, 'reserved': 1} and not view_after['ready']
    view('/releases', {'expected': 3}, (409, 'not_ready'))
    assert view('/releases/2/sorter') == first_bytes

    # 同一更正编号同内容幂等，换内容不允许复用；该批次只接受一次更正。
    assert fix('C1', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, reason='首测设备横轴镜像录反')['duplicate']
    fix('C1', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, reason='另一事由',
        error=(409, 'correction_conflict'))
    fix('C5', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, error=(409, 'correction_exists'))
    assert view()['revision'] == 3

    # 旧复测链保留作历史，并注明其依赖的原行已不在此点成立。
    middle = view('/cells/-1/0')
    assert middle['state'] == 'review'
    assert [e['event'] for e in middle['chain']] == ['measurement', 'measurement', 'correction']
    assert middle['chain'][1]['batch'] == 'T01' and middle['chain'][2]['batch'] == 'S01'
    assert middle['chain'][2]['superseded'][0] == ['S01', -1, 0]
    outer = view('/cells/-2/0')
    assert outer['chain'][-1]['record']['x'] == 2 and outer['chain'][-1]['previous']['site'] == [2, 0]
    assert view('/cells/2/0')['chain'][-1]['record']['x'] == -2
    assert view('/cells/2/0')['chain'][-1]['previous']['site'] == [-2, 0]

    # 旧父批次仍叫 S 也不能径行签出：重发旧复测批次无济于事。
    assert sub('T01', '-1,0,ok,10,60,S01\n1,0,ok,10,60,S01\n', 'repeat')['duplicate']
    assert not view()['ready'] and view()['counts']['review'] == 2

    # 旧复测批次 T01 已整条归档；新补测若仍以它为父会被拒，因为父须是当前待复测证据。
    sub('V01', '-1,0,ok,10,60,T01\n', 'repeat', error=(409, 'repeat_parent'))
    assert not view()['ready']

    # 以更正后归属的首测为父重新补测，中间两点取得合规复测后才能再签。
    sub('U01', '-1,0,ok,10,60,S01\n1,0,ok,10,60,S01\n', 'repeat')
    assert view()['counts'] == {'b': 4, 'reserved': 1} and view()['ready']
    view('/releases', {'expected': 4})

    # 两版对照：外侧点同档同批，但设备原坐标已经互换，属于来源变化。
    changes = {(c['site'][0], c['site'][1]): c for c in view('/compare/2/4')['changes']}
    assert sorted(changes) == [(-2, 0), (-1, 0), (1, 0), (2, 0)]
    assert changes[(-2, 0)]['kind'] == 'evidence' and changes[(-2, 0)]['from_device'] == [-2, 0] \
        and changes[(-2, 0)]['to_device'] == [2, 0] and changes[(-2, 0)]['before'] == changes[(-2, 0)]['after'] == 'b'
    assert changes[(2, 0)]['from_device'] == [2, 0] and changes[(2, 0)]['to_device'] == [-2, 0]
    assert changes[(-1, 0)]['from_batch'] == 'T01' and changes[(-1, 0)]['to_batch'] == 'U01'

    # 两份签件各自可下载、可独立复算，旧签件不含更正事件。
    assert view('/releases/2/replay')['verified'] and view('/releases/2/replay')['rows'] == 4
    assert view('/releases/4/replay')['verified'] and view('/releases/4/replay')['rows'] == 4
    new_bytes = view('/releases/4/sorter')
    assert new_bytes != first_bytes and new_bytes.decode().splitlines()[1:] == [
        '-2,0,b,S01', '-1,0,b,U01', '1,0,b,U01', '2,0,b,S01']
    old_manifest = json.loads(view('/releases/2/manifest'))
    assert not any(e['kind'] == 'correction' for e in old_manifest['payload']['events'])
    new_manifest = json.loads(view('/releases/4/manifest'))
    assert any(e['kind'] == 'correction' for e in new_manifest['payload']['events'])
    # 原始量测行与旧签件都保留：S 的原始设备坐标仍是 −2…2。
    stored = [e['body'] for e in new_manifest['payload']['events'] if e['kind'] == 'batch' and e['body']['id'] == 'S01'][0]
    assert sorted((r['x'], r['y']) for r in stored['rows']) == [(-2, 0), (-1, 0), (1, 0), (2, 0)]
    assert stored['frame'] == identity_frame


def hold_blocks_after_correction(s):
    shape = {'id': 'LX-22', 'spans': [[0, -2, 2]], 'reserved': [[0, 0]], 'rule': 'sable', 'rule_version': 1}
    s.request('/api/diegrade/wafers', shape)
    view, sub, fix = series(s, 'LX-22')
    sub('S01', '-2,0,ok,10,60,\n-1,0,ok,10,60,\n1,0,ok,10,60,\n2,0,ok,10,60,\n')
    assert view()['ready']
    view('/holds', {'expected': 1, 'id': 'H01', 'site': [2, 0], 'reason': '边缘复查'})
    fix('C01', 'S01', {'offset': [0, 0], 'turn': 0, 'mirror': True}, reason='横轴录反')
    assert view()['counts']['review'] == 1 and not view()['ready']
    blocked = view('/cells/2/0')
    assert blocked['state'] == 'review' and any(e['event'] == 'hold' for e in blocked['chain'])
    view('/releases', {'expected': view()['revision']}, (409, 'not_ready'))


STEPS = (rules, wafer, preview, ingest, grid, trace, seal, replay, export, hold, compare, correct,
         hold_blocks_after_correction)


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
