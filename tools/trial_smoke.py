"""以临时库演练规则试评旁路（固定原签件与备选规则版本）。

覆盖：
1. 全边缘晶圆 P/Q/R 在旧规则下签出；
2. 新规则下逐点试评：P 触发首测复测带、Q 的旧复测无合法待复测父、R 保留甲档；
3. 探针按补测单送回，外单/错点整批不生效、开路保留、原样重送幂等；
4. 正确补单关闭后逐点判退/乙档且可追溯；
5. 旧复核以当时证据裁决；试评不推进现场代次、不改原签件、不产分选指令。
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from diegrade.database import Database
from diegrade.httpd import make_server

HEADER = 'x,y,contact,leak_na,margin_mv,parent\n'
FRAME = {'offset': [0, 0], 'turn': 0, 'mirror': False}


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'

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
        assert error is None, f'{path} 应失败于 {error}'
        return json.loads(raw) if mime == 'application/json' else raw

    def batch(self, wafer, bid, records, kind='screen', frame=None, expected=None):
        body = {'expected': self.rev if expected is None else expected,
                'batch': {'id': bid, 'kind': kind, 'frame': frame or FRAME, 'csv': HEADER + records}}
        result = self.request(f'/api/diegrade/wafers/{wafer}/batches', body)
        self.rev = result['revision']
        return result


def rules_and_wafers(c):
    old = {'name': 'edge-eval', 'version': 1, 'leak_limit': 100, 'margin_min': 50,
           'grade_a': 90, 'edge_add': 0, 'guard': 3}
    new = {**old, 'version': 2, 'margin_min': 60}
    looser = {'name': 'edge-eval', 'version': 3, 'leak_limit': 100, 'margin_min': 40,
              'grade_a': 90, 'edge_add': 0, 'guard': 3}
    for rule in (old, new, looser):
        c.request('/api/diegrade/rules', rule)
    # EW-1 一行三个点：四邻域缺邻，全部为边缘点；另两片按各自点数与规则版本注册。
    c.request('/api/diegrade/wafers',
              {'id': 'EW-1', 'spans': [[0, 0, 2]], 'reserved': [],
               'rule': 'edge-eval', 'rule_version': 1})
    c.request('/api/diegrade/wafers',
              {'id': 'EW-2', 'spans': [[0, 0, 1]], 'reserved': [],
               'rule': 'edge-eval', 'rule_version': 1})
    c.request('/api/diegrade/wafers',
              {'id': 'EW-3', 'spans': [[0, 0, 0]], 'reserved': [],
               'rule': 'edge-eval', 'rule_version': 3})


def build_signed_wafer(c):
    c.rev = 0
    # P=60 旧规则乙档；Q=50 旧规则待复测；R=93 甲档。
    c.batch('EW-1', 'S1', '0,0,ok,10,60,\n1,0,ok,10,50,\n2,0,ok,10,93,\n')
    # Q 以其旧首测 S1 为父补到 70，旧规则乙档。
    c.batch('EW-1', 'R1', '1,0,ok,10,70,S1\n', 'repeat')
    assert c.rev == 2
    c.request('/api/diegrade/wafers/EW-1/releases', {'expected': 2})


def initial_trial(c):
    body = {'id': 'T1', 'release': 2, 'rule': 'edge-eval', 'rule_version': 2}
    view = c.request('/api/diegrade/wafers/EW-1/trials', body)
    # 幂等：同签件同规则重复创建返回同一份试评，且现场代次不变。
    assert c.request('/api/diegrade/wafers/EW-1/trials', body)['orders'] == view['orders']
    assert c.request('/api/diegrade/wafers/EW-1')['revision'] == 2
    cells = {tuple(cell['site']): cell for cell in view['cells']}
    p, q, r = cells[(0, 0)], cells[(1, 0)], cells[(2, 0)]
    assert all(cell['edge'] for cell in (p, q, r))
    assert p['state'] == 'review' and '首测复测带' in p['basis']
    assert q['state'] == 'review' and q['order']['reason_code'] == 'history_broken'
    assert 'R1' in q['order']['first_ref'] and '待复测' in q['order']['reason']
    assert r['state'] == 'a' and r['order'] is None
    assert [order['id'] for order in view['orders']] == ['O01', 'O02']
    assert {order['id']: order['site'] for order in view['orders']} == {'O01': [0, 0], 'O02': [1, 0]}
    # 逐点追溯：P 只有一条被接纳的历史量测；Q 的旧复测被跳过且写明原因。
    assert len(p['evidence']) == 1 and not p['skipped']
    assert q['evidence'][0]['batch'] == 'S1'
    assert q['skipped'][0]['batch'] == 'R1' and q['skipped'][0]['accepted'] is False
    # 试评不产生任何生产分选指令，也不能借用生产签出接口。
    assert c.request('/api/diegrade/wafers/EW-1/trials') == [
        {'id': 'T1', 'release': 2, 'rule': 'edge-eval', 'rule_version': 2}]
    c.request('/api/diegrade/wafers/EW-1/trials/T1/cells/9/9', error=(404, 'site_missing'))
    c.request('/api/diegrade/wafers/EW-1/trials/T99', error=(404, 'trial_missing'))
    return view


def wrong_submissions(c):
    path = '/api/diegrade/wafers/EW-1/trials/T1/batches'
    # 先建第二轮试评 T2，制造“同一原签件上另一轮试评的补测单”。
    c.request('/api/diegrade/wafers/EW-1/trials',
              {'id': 'T2', 'release': 2, 'rule': 'edge-eval', 'rule_version': 2})
    t2_order = c.request('/api/diegrade/wafers/EW-1/trials/T2')['orders'][0]['id']
    # P=55 合规，但 Q 引用 T2 的补测单：外单混入，本次整批不生效。
    mixed = {'batch': {'id': 'B1', 'kind': 'repeat', 'frame': FRAME,
                       'csv': HEADER + f'0,0,ok,10,55,O01\n1,0,ok,10,70,{t2_order}\n'}}
    c.request(path, mixed, error=(422, 'trial_order_ref'))
    # 错点：R 没有未关闭补测单，整批不生效。
    wrong = {'batch': {'id': 'B2', 'kind': 'repeat', 'frame': FRAME,
                       'csv': HEADER + '0,0,ok,10,55,O01\n2,0,ok,10,95,O02\n'}}
    c.request(path, wrong, error=(422, 'trial_order'))
    view = c.request('/api/diegrade/wafers/EW-1/trials/T1')
    assert all(order['status'] == 'open' for order in view['orders'])
    assert view['batches'] == [] and view['ready'] is False


def open_and_resend(c):
    path = '/api/diegrade/wafers/EW-1/trials/T1/batches'
    # P 开路：补测单保留原因继续等待，不关闭；回来件已入账。
    opened = {'batch': {'id': 'B3', 'kind': 'repeat', 'frame': FRAME,
                        'csv': HEADER + '0,0,open,,,O01\n'}}
    result = c.request(path, opened)
    assert result['changes'] == [{'site': [0, 0], 'state': 'review', 'why': '接触开路，无有效测值'}]
    view = c.request('/api/diegrade/wafers/EW-1/trials/T1')
    assert view['orders'][0]['status'] == 'open' and '开路' in view['orders'][0]['reason']
    # 原样重送不重复入账。
    assert c.request(path, opened)['duplicate'] is True
    assert len(c.request('/api/diegrade/wafers/EW-1/trials/T1')['batches']) == 1
    # 同编号换内容拒绝复用。
    changed = {'batch': {'id': 'B3', 'kind': 'repeat', 'frame': FRAME,
                         'csv': HEADER + '0,0,ok,10,56,O01\n'}}
    c.request(path, changed, error=(409, 'batch_conflict'))


def correct_returns(c):
    path = '/api/diegrade/wafers/EW-1/trials/T1/batches'
    body = {'batch': {'id': 'B4', 'kind': 'repeat', 'frame': FRAME,
                      'csv': HEADER + '0,0,ok,10,55,O01\n1,0,ok,10,70,O02\n'}}
    result = c.request(path, body)
    assert {(row['site'][0], row['site'][1]): row['state'] for row in result['changes']} == {
        (0, 0): 'reject', (1, 0): 'b'}
    view = c.request('/api/diegrade/wafers/EW-1/trials/T1')
    assert all(order['status'] == 'closed' for order in view['orders']) and view['ready'] is True
    cells = {tuple(cell['site']): cell for cell in view['cells']}
    p, q = cells[(0, 0)], cells[(1, 0)]
    assert p['state'] == 'reject' and p['evidence'][-1]['batch'] == 'B4'
    assert q['state'] == 'b' and q['evidence'][-1]['batch'] == 'B4'
    # 历史证据与本次补测都能逐点追溯。
    trace = c.request('/api/diegrade/wafers/EW-1/trials/T1/cells/1/0')
    assert [item['batch'] for item in trace['evidence'] if item['event'] == 'measurement'] == ['S1']
    assert [item['batch'] for item in trace['evidence'] if item['event'] == 'trial_measurement'] == ['B4']
    assert trace['skipped'][0]['batch'] == 'R1'
    # 原晶圆现场代次与原签件均未被试评推进或改动。
    assert c.request('/api/diegrade/wafers/EW-1')['revision'] == 2
    release = c.request('/api/diegrade/wafers/EW-1/releases/2/replay')
    assert release['verified'] is True


def hold_evidence(c):
    """旧复核能否成立以当时证据为准：只重开当时已终判的点。"""
    c.rev = 0
    # 旧规则（下限 50、甲档 90、带 3）下：X=57、Y=60 均为乙档终判。
    # 新规则下限 60：X=57 落入首测复测带（57–63），复核可真正重开；Y=60 同在复测带但未重开。
    c.batch('EW-2', 'S2', '0,0,ok,10,57,\n1,0,ok,10,60,\n')
    # 只对 X 做有编号复核重开；Y 不重开，保留原终判。
    c.request('/api/diegrade/wafers/EW-2/holds',
              {'expected': 1, 'id': 'H1', 'site': [0, 0], 'reason': '甲档抽检'})
    c.rev = 2
    c.batch('EW-2', 'R2', '0,0,ok,10,80,S2\n', 'repeat')
    c.request('/api/diegrade/wafers/EW-2/releases', {'expected': 3})
    view = c.request('/api/diegrade/wafers/EW-2/trials',
                     {'id': 'T3', 'release': 3, 'rule': 'edge-eval', 'rule_version': 2})
    cells = {tuple(cell['site']): cell for cell in view['cells']}
    x, y = cells[(0, 0)], cells[(1, 0)]
    # X：历史在新规则下完整（53 待复测→合法复核真正重开→80 乙档），无需补测单。
    assert x['state'] == 'b' and x['order'] is None
    assert [item['event'] for item in x['evidence']] == ['measurement', 'hold', 'measurement']
    assert x['evidence'][1]['reopened'] is True
    # Y：旧首测 60 落在新规则首测复测带且未被复核重开，仍待复测，须发单并说明原因。
    assert y['state'] == 'review' and y['order']['reason_code'] == 'still_review'
    assert '首测复测带' in y['order']['reason']


def invalid_hold(c):
    """新规则下仍为终档的点，旧复核不能重开，其后的复测失去待复测父状态。"""
    c.rev = 0
    # EW-3 按第 3 版规则（下限 40）注册：70 在该版为乙档终判，复核合法并复测到 75。
    c.batch('EW-3', 'S3', '0,0,ok,10,70,\n')
    c.request('/api/diegrade/wafers/EW-3/holds',
              {'expected': 1, 'id': 'H9', 'site': [0, 0], 'reason': '复验'})
    c.rev = 2
    c.batch('EW-3', 'R3', '0,0,ok,10,75,S3\n', 'repeat')
    c.request('/api/diegrade/wafers/EW-3/releases', {'expected': 3})
    view = c.request('/api/diegrade/wafers/EW-3/trials',
                     {'id': 'T9', 'release': 3, 'rule': 'edge-eval', 'rule_version': 2})
    cell = view['cells'][0]
    # 新规则（下限 60）下首测 70 仍是乙档终判，复核 H9 不产生重开；
    # 于是 R3 提交时没有合法待复测父状态，成为最早失去资格的记录并触发补测单。
    assert cell['state'] == 'review' and cell['order']['reason_code'] == 'history_broken'
    assert cell['order']['first_ref'] == 'R3'
    assert cell['skipped'][0]['event'] == 'measurement' and cell['skipped'][0]['batch'] == 'R3'
    assert cell['evidence'][1]['event'] == 'hold' and cell['evidence'][1]['reopened'] is False


STEPS = (rules_and_wafers, build_signed_wafer, initial_trial, wrong_submissions,
         open_and_resend, correct_returns, hold_evidence, invalid_hold)


def main():
    with TemporaryDirectory(prefix='diegrade-trial-') as temp, \
            make_server(Database(Path(temp) / 'db'), 0) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = Client(server.server_port)
            for flow in STEPS:
                flow(client)
                print(f'通过：{flow.__name__}')
        finally:
            server.shutdown()
            worker.join()


if __name__ == '__main__':
    main()
