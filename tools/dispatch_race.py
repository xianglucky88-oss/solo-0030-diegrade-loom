import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path
from urllib.request import Request, urlopen

from diegrade.database import Database
from diegrade.httpd import make_server

HEADER = 'x,y,contact,leak_na,margin_mv,parent\n'


def post(port, path, body):
    req = Request(f'http://127.0.0.1:{port}/api/diegrade{path}',
                  data=json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json'}, method='POST')
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def setup():
    tmp = tempfile.TemporaryDirectory()
    db = Database(Path(tmp.name) / 'race.sqlite3')
    server = make_server(db, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    identity = {'offset': [0, 0], 'turn': 0, 'mirror': False}
    post(port, '/rules', {'name': 't', 'version': 1, 'leak_limit': 100,
                          'margin_min': 40, 'grade_a': 80, 'edge_add': 0, 'guard': 5})
    post(port, '/wafers', {'id': 'W', 'spans': [[0, -2, 3]], 'reserved': [],
                           'rule': 't', 'rule_version': 1})
    screen = '-1,0,open,,,\n0,0,ok,5,42,\n1,0,open,,,\n2,0,ok,5,42,\n'
    post(port, '/wafers/W/batches', {'expected': 0, 'batch': {
        'id': 'S1', 'kind': 'screen', 'frame': identity, 'csv': HEADER + screen}})
    post(port, '/stations', {'id': 'JIA', 'turn': 2, 'quota': 2,
                             'routes': [[[0, 0], [-2, 0]], [[1, 0], [0, 0]]]})
    post(port, '/stations', {'id': 'YI', 'frame': identity, 'limit': 2,
                             'routes': [[[-1, 0], [1, 0]], [[1, 0], [2, 0]]]})
    return tmp, server, thread, port


def main():
    for trial in range(10):
        tmp, server, thread, port = setup()
        barrier = Barrier(2)

        def dispatch(station):
            barrier.wait()
            return post(port, '/wafers/W/dispatch', {'station': station})

        with ThreadPoolExecutor(2) as pool:
            a, b = list(pool.map(dispatch, ('JIA', 'YI')))
        sites_a = {tuple(x) for x in a['sites']}
        sites_b = {tuple(x) for x in b['sites']}
        assert not (sites_a & sites_b), (a, b)
        assert len(sites_a) == len(sites_b) == 2, (a, b)
        all_sites = sites_a | sites_b
        assert all_sites == {(-1, 0), (0, 0), (1, 0), (2, 0)}, all_sites
        by_station = {x['station_id']: x for x in (a, b)}
        jia, yi = by_station['JIA'], by_station['YI']
        if yi['open_count'] == 2:
            assert yi['route_id'] == 'R1'
            assert jia['route_id'] == 'R1' and jia['site_count'] == 2
        else:
            assert jia['route_id'] == 'R2' and jia['sites'] == [[-1, 0], [0, 0]]
            assert yi['route_id'] == 'R2' and yi['sites'] == [[1, 0], [2, 0]]
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        tmp.cleanup()
    print('RACE PASS')


if __name__ == '__main__':
    main()
