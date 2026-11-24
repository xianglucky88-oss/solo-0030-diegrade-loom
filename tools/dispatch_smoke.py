import json
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from diegrade.database import Database
from diegrade.httpd import make_server

HEADER = 'x,y,contact,leak_na,margin_mv,parent\n'


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}/api/diegrade'

    def call(self, path, body=None, method=None, expect=None):
        data = None if body is None else json.dumps(body).encode()
        req = Request(self.base + path, data=data, method=method or ('POST' if body is not None else 'GET'),
                      headers={'Content-Type': 'application/json'})
        try:
            with urlopen(req, timeout=10) as r:
                raw = r.read()
                value = json.loads(raw) if raw else None
                print('OK', path, json.dumps(value, ensure_ascii=False)[:500])
                return value
        except HTTPError as e:
            raw = e.read().decode()
            try:
                value = json.loads(raw)
            except Exception:
                value = raw
            print('ERR', e.code, path, value)
            if expect is None:
                raise
            assert e.code == expect[0], (e.code, value)
            assert value['error'] == expect[1], value
            return value


def main():
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / 'test.sqlite3')
    server = make_server(db, 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    c = Client(server.server_port)
    try:
        c.call('/rules', {
            'name': 't', 'version': 1, 'leak_limit': 100, 'margin_min': 40,
            'grade_a': 80, 'edge_add': 0, 'guard': 5,
        })
        c.call('/wafers', {
            'id': 'W1',
            'spans': [[-1, -2, 3], [0, -2, 3], [1, -2, 3]],
            'reserved': [], 'rule': 't', 'rule_version': 1,
        })
        identity = {'offset': [0, 0], 'turn': 0, 'mirror': False}
        turn180 = {'offset': [0, 0], 'turn': 2, 'mirror': False}
        screen = (
            '-1,0,open,,,\n'
            '0,0,ok,5,42,\n'
            '1,0,open,,,\n'
            '2,0,ok,5,42,\n'
        )
        c.call('/wafers/W1/batches', {'expected': 0, 'batch': {
            'id': 'S1', 'kind': 'screen', 'frame': identity, 'csv': HEADER + screen}})

        c.call('/stations', {
            'id': 'JIA', 'frame': turn180, 'quota': 2,
            'routes': [{'id': 'left', 'points': [[0, 0], [-2, 0]]},
                       {'id': 'ab', 'points': [[1, 0], [0, 0]]}],
        })
        c.call('/stations', {
            'id': 'YI', 'frame': identity, 'quota': 2,
            'routes': [[[-1, 0], [1, 0]], [[1, 0], [2, 0]]],
        })

        jia_order = c.call('/wafers/W1/dispatch', {'station': 'JIA'})
        assert jia_order['sites'] == [[-1, 0], [0, 0]], jia_order
        assert jia_order['route_id'] == 'ab'
        yi_order = c.call('/wafers/W1/work-orders', {'station_id': 'YI'})
        assert yi_order['sites'] == [[1, 0], [2, 0]], yi_order

        c.call('/wafers/W1/batches', {'expected': 1, 'batch': {
            'id': 'ORDINARY-A', 'kind': 'repeat', 'frame': identity,
            'csv': HEADER + '-1,0,open,,,S1\n'}}, expect=(409, 'site_locked'))

        bad_yi_body = {'station': 'YI', 'batch': {
            'id': 'YBAD', 'kind': 'repeat', 'frame': identity,
            'csv': HEADER + '1,0,open,,,S1\n-1,0,open,,,S1\n'}}
        c.call(f"/work-orders/{yi_order['id']}/complete", bad_yi_body, expect=(422, 'order_sites'))
        trace_c = c.call('/wafers/W1/cells/1/0')
        assert len(trace_c['chain']) == 1, trace_c

        good_yi_body = {'station': 'YI', 'batch': {
            'id': 'Y1', 'kind': 'repeat', 'frame': identity,
            'csv': HEADER + '1,0,open,,,S1\n2,0,ok,5,90,S1\n'}}
        yi_result = c.call(f"/work-orders/{yi_order['id']}/receipt", good_yi_body)
        assert [x['state'] for x in yi_result['changes']] == ['review', 'a']

        jia_body = {'station_id': 'JIA', 'batch': {
            'id': 'J1', 'kind': 'repeat', 'frame': turn180,
            'csv': HEADER + '1,0,open,,,S1\n0,0,ok,5,90,S1\n'}}
        jia_result = c.call(f"/orders/{jia_order['id']}/submit", jia_body)
        assert [x['state'] for x in jia_result['changes']] == ['review', 'a']

        next_order = c.call('/wafers/W1/dispatch', {'station': 'JIA'})
        assert next_order['sites'] == [[-1, 0]], next_order
        assert next_order['assignments'][0]['parent'] == 'J1', next_order

        c.call(f"/work-orders/{next_order['id']}/complete", {'station': 'JIA', 'batch': {
            'id': 'JBAD', 'kind': 'repeat', 'frame': turn180,
            'csv': HEADER + '1,0,open,,,S1\n'}}, expect=(409, 'repeat_parent'))

        replay = c.call(f"/work-orders/{jia_order['id']}/complete", jia_body)
        assert replay == jia_result, (replay, jia_result)
        locked_order = c.call(f"/work-orders/{next_order['id']}")
        assert locked_order['status'] == 'open'

        # Unlocked C can still be handled by an ordinary repeat batch.
        c.call('/wafers/W1/batches', {'expected': 3, 'batch': {
            'id': 'C2', 'kind': 'repeat', 'frame': identity,
            'csv': HEADER + '1,0,open,,,Y1\n'}})
        replay2 = c.call(f"/work-orders/{jia_order['id']}/complete", jia_body)
        assert replay2 == jia_result
        locked_order2 = c.call(f"/work-orders/{next_order['id']}")
        assert locked_order2['status'] == 'open'
        batch_replay = c.call('/wafers/W1/batches', {'expected': 99, 'batch': jia_body['batch']})
        assert batch_replay == jia_result
        locked_order3 = c.call(f"/work-orders/{next_order['id']}")
        assert locked_order3['status'] == 'open'
        print('SCENARIO PASS')
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2)
        tmp.cleanup()


if __name__ == '__main__':
    main()
