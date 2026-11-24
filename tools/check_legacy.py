"""核对旧库迁移、旧封存件回放与装夹周期伪造对照。"""
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from diegrade import archives, rules, wafers
from diegrade.codec import decode, digest, encode
from diegrade.database import Database
from diegrade.errors import DomainError
from diegrade.cycles import pose_spec

RULE = {'name': 'edgecal', 'version': 1, 'leak_limit': 100, 'margin_min': 40,
        'grade_a': 80, 'edge_add': 10, 'guard': 3}
WAFER = {'id': 'C3', 'spans': [[-1, -1, 1], [0, -1, 1], [1, -1, 1]],
         'reserved': [[0, 0], [0, 1]], 'rule': 'edgecal', 'rule_version': 1}


def legacy_body():
    """一份 release.1 结构：没有 cycles 字段，位姿直接写在批次里。"""
    spec = {**WAFER, 'rule': RULE, 'reserved': [[0, 0], [0, 1]]}
    batch = {'id': 'L1', 'kind': 'screen', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
             'cycle': None, 'rows': [
                 {'x': x, 'y': y, 'contact': 'ok', 'parent': '', 'leak_na': 10, 'margin_mv': margin}
                 for x, y, margin in [(-1, 0, 96), (1, 0, 75), (1, 1, 90), (0, -1, 60),
                                      (-1, 1, 90), (1, -1, 75), (-1, -1, 90)]]}
    events = [{'revision': 1, 'kind': 'batch', 'body': batch}]
    from diegrade.timeline import replay_timeline
    from diegrade.sorter import assignments
    rows = assignments(replay_timeline(spec, events))
    body = {'format': 'diegrade.release.1', 'revision': 1, 'wafer': spec, 'events': events, 'rows': rows}
    return {'payload': body, 'sha256': digest(body)}


def check_legacy():
    release = legacy_body()
    result = archives.verify(release)
    assert result['verified'] and result['rows'] == 7 and result['cycles'] == [], result


def check_migration():
    with TemporaryDirectory() as temp:
        path = Path(temp) / 'old.sqlite3'
        # 造 v1 库：event 表只允许 batch/hold。
        conn = sqlite3.connect(path)
        conn.executescript('''
            CREATE TABLE rules (name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (name, version));
            CREATE TABLE wafers (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE events (wafer TEXT NOT NULL, revision INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('batch', 'hold')),
                ref TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (wafer, revision), UNIQUE (wafer, kind, ref));
            CREATE TABLE releases (wafer TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (wafer, revision));
            PRAGMA user_version = 1;
        ''')
        conn.execute('INSERT INTO rules VALUES (?, ?, ?)', ('edgecal', 1, encode(RULE)))
        conn.execute('INSERT INTO wafers VALUES (?, ?, ?)', ('C3', encode({**WAFER, 'rule': RULE}), 0))
        conn.commit()
        conn.close()
        db = Database(path)
        with db.transaction(write=True) as conn:
            from diegrade import intake
            out = intake.register_cycle(conn, 'C3', {'expected': 0, 'cycle': {
                'id': 'K1', 'poses': [[0, False], [2, False]], 'markers': {'M0': [0, 0], 'M1': [0, 1]}}})
            assert out['revision'] == 1, out


def check_forgery():
    from diegrade import intake
    with TemporaryDirectory() as temp:
        db = Database(Path(temp) / 'db.sqlite3')
        with db.transaction(write=True) as conn:
            rules.register(conn, RULE)
            wafers.register(conn, WAFER)
            intake.register_cycle(conn, 'C3', {'expected': 0, 'cycle': {
                'id': 'K1', 'poses': [[0, False], [2, False]], 'markers': {'M0': [0, 0], 'M1': [0, 1]}}})
            intake.observe(conn, 'C3', {'expected': 1, 'cycle': 'K1', 'marker': 'M0', 'device': [0, 0]})
            intake.observe(conn, 'C3', {'expected': 2, 'cycle': 'K1', 'marker': 'M1', 'device': [0, -1]})
            head = 'x,y,contact,leak_na,margin_mv,parent\n'
            intake.intake(conn, 'C3', {'expected': 3, 'batch': {'id': 'S1', 'kind': 'screen', 'cycle': 'K1',
                'csv': head + '1,0,ok,10,96,\n-1,0,ok,10,75,\n'}}, write=True)
            rest = head + '1,1,ok,10,90,\n0,1,ok,10,75,\n-1,1,ok,10,90,\n1,-1,ok,10,75,\n-1,-1,ok,10,90,\n'
            intake.intake(conn, 'C3', {'expected': 4, 'batch': {'id': 'S2', 'kind': 'screen', 'cycle': 'K1',
                'csv': rest}}, write=True)
            release = archives.seal(conn, 'C3', {'expected': 5})
            good = {(r['x'], r['y']): r['bin'] for r in release['payload']['rows']}
            assert good[(-1, 0)] == 'a' and good[(1, 0)] == 'b'

            # 把测量按 0 度处理：重建东西两点与受影响行，再重算包内摘要。
            forged = json.loads(encode(release))
            body = forged['payload']
            spec = body['wafer']
            from diegrade.timeline import replay_timeline
            from diegrade.sorter import assignments
            zero = {'offset': [0, 0], 'turn': 0, 'mirror': False}
            events = []
            for event in body['events']:
                event = json.loads(encode(event))
                if event['kind'] == 'batch' and event['body'].get('cycle') == 'K1':
                    event['body']['frame'] = zero
                    event['body']['cycle'] = None
                events.append(event)
            body['events'] = events
            body.pop('cycles')
            # 按 0 度处理会把测量挪到保留点，重建本身就该失败；若勉强能重建，
            # 摘要对齐后核验仍须拒绝。
            try:
                body['rows'] = assignments(replay_timeline(spec, events))
            except DomainError as exc:
                assert exc.code == 'mapped_site', exc.code
            else:
                forged['sha256'] = digest(body)
                try:
                    archives.verify(forged)
                    raise AssertionError('0 度伪造应被拒')
                except DomainError as exc:
                    assert exc.code in ('release_replay', 'cycle_pose_conflict', 'mapped_site'), exc.code

            # 只把随件位姿改成 0 度，保留确认时的原始标记与全部量测，摘要也对齐：
            # 同一份标记证据唯一支持 180 度，核验必须拒绝。
            forged2 = json.loads(encode(release))
            forged2['payload']['cycles'][0]['pose'] = zero
            forged2['sha256'] = digest(forged2['payload'])
            try:
                archives.verify(forged2)
                raise AssertionError('伪造锁定位姿应被拒')
            except DomainError as exc:
                assert exc.code == 'release_replay', exc.code

            # 连确认时的标记一起缩减到中心标记，仍不能把 0 度说成已确认。
            forged2b = json.loads(encode(release))
            forged2b['payload']['cycles'][0]['pose'] = zero
            forged2b['payload']['cycles'][0]['lock_observations'] = [
                {'marker': 'M0', 'device': [0, 0], 'revision': 2}]
            forged2b['sha256'] = digest(forged2b['payload'])
            try:
                archives.verify(forged2b)
                raise AssertionError('不唯一证据不得支撑锁定')
            except DomainError as exc:
                assert exc.code == 'release_replay', exc.code

            # 只改封存的位姿采用记录、把 S1 说成 0 度，摘要对齐后仍须拒绝。
            forged2c = json.loads(encode(release))
            for item in forged2c['payload']['usage']:
                if item['id'] == 'S1':
                    item['pose'] = zero
            forged2c['sha256'] = digest(forged2c['payload'])
            try:
                archives.verify(forged2c)
                raise AssertionError('伪造位姿采用应被拒')
            except DomainError as exc:
                assert exc.code == 'release_replay', exc.code

            # 丢弃标记证据、只留量测，也必须拒绝。
            forged3 = json.loads(encode(release))
            forged3['payload']['events'] = [e for e in forged3['payload']['events'] if e['kind'] in ('batch',)]
            forged3['payload']['cycles'] = []
            # 批次仍引用未登记的周期：连行都重建不出来。
            try:
                forged3['payload']['rows'] = assignments(replay_timeline(spec, forged3['payload']['events']))
            except DomainError as exc:
                assert exc.code in ('cycle_missing', 'cycle_unconfirmed'), exc.code
            forged3['sha256'] = digest(forged3['payload'])
            try:
                archives.verify(forged3)
                raise AssertionError('丢弃标记证据应被拒')
            except DomainError as exc:
                assert exc.code in ('release_replay', 'cycle_unconfirmed', 'cycle_missing'), exc.code


check_legacy()
check_migration()
check_forgery()
print('迁移、旧件与伪造对照均通过')
