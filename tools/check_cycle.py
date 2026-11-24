"""核对装夹周期场景：3×3 晶圆、对准标记、位姿确认与接收方复算。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from diegrade import archives, intake, rules, wafers
from diegrade.database import Database
from diegrade.errors import DomainError

HEADER = 'x,y,contact,leak_na,margin_mv,parent\n'
RULE = {'name': 'edgecal', 'version': 1, 'leak_limit': 100, 'margin_min': 40,
        'grade_a': 80, 'edge_add': 10, 'guard': 3}
WAFER = {'id': 'C3', 'spans': [[-1, -1, 1], [0, -1, 1], [1, -1, 1]],
         'reserved': [[0, 0], [0, 1]], 'rule': 'edgecal', 'rule_version': 1}
CYCLE = {'id': 'K1', 'poses': [[0, False], [2, False]], 'markers': {'M0': [0, 0], 'M1': [0, 1]}}


def run(conn):
    rules.register(conn, RULE)
    wafers.register(conn, WAFER)
    out = intake.register_cycle(conn, 'C3', {'expected': 0, 'cycle': CYCLE})
    assert out['revision'] == 1 and out['state']['state'] == 'open', out
    out = intake.observe(conn, 'C3', {'expected': 1, 'cycle': 'K1', 'marker': 'M0', 'device': [0, 0]})
    assert out['state']['state'] == 'undetermined' and out['revision'] == 2, out
    # 只收到中心标记时零度与 180 度都可能；首测必须被拒绝，且代次不变。
    try:
        intake.intake(conn, 'C3', {'expected': 2, 'batch': {
            'id': 'S1', 'kind': 'screen', 'cycle': 'K1',
            'csv': HEADER + '1,0,ok,10,96,\n-1,0,ok,10,75,\n'}}, write=True)
        raise AssertionError('确认前应拒绝')
    except DomainError as exc:
        assert exc.code == 'cycle_unconfirmed', exc.code
    assert wafers.load_wafer(conn, 'C3')[1] == 2
    # 同一标记重送不增加证据。
    out = intake.observe(conn, 'C3', {'expected': 2, 'cycle': 'K1', 'marker': 'M0', 'device': [0, 0]})
    assert out['duplicate'] and out['revision'] == 2, out
    # M1 从 (0,-1) 报到，唯一可行位姿 180 度。
    out = intake.observe(conn, 'C3', {'expected': 2, 'cycle': 'K1', 'marker': 'M1', 'device': [0, -1]})
    assert out['newly_locked'] and out['state']['state'] == 'confirmed', out
    pose = out['state']['pose']
    assert pose == {'turn': 2, 'mirror': False, 'offset': [0, 0]}, pose
    # 与锁定位姿冲突的观测被拒。
    try:
        intake.observe(conn, 'C3', {'expected': 3, 'cycle': 'K1', 'marker': 'M1', 'device': [0, 1]})
        raise AssertionError('冲突观测应被拒')
    except DomainError as exc:
        assert exc.code == 'cycle_observation_conflict', exc.code
    # 设备 (1,0) -> 物理西点 (-1,0) 96 mV 甲；(-1,0) -> 东点 (1,0) 75 mV 乙。
    out = intake.intake(conn, 'C3', {'expected': 3, 'batch': {
        'id': 'S1', 'kind': 'screen', 'cycle': 'K1',
        'csv': HEADER + '1,0,ok,10,96,\n-1,0,ok,10,75,\n'}}, write=True)
    assert out['revision'] == 4, out
    # 其余可测点补齐后方可签出。
    rest = [(1, 1, 96), (0, 1, 75), (-1, 1, 96), (1, -1, 75), (-1, -1, 96)]
    body = HEADER + ''.join(f'{x},{y},ok,10,{m},\n' for x, y, m in rest)
    intake.intake(conn, 'C3', {'expected': 4, 'batch': {'id': 'S2', 'kind': 'screen', 'cycle': 'K1', 'csv': body}}, write=True)
    release = archives.seal(conn, 'C3', {'expected': 5})
    rows = {(r['x'], r['y']): r['bin'] for r in release['payload']['rows']}
    assert rows[(-1, 0)] == 'a' and rows[(1, 0)] == 'b', rows
    result = archives.verify(release)
    assert result['verified'] and result['rows'] == 7, result
    # 批次自带位姿与锁定位姿冲突：拒绝来件。
    try:
        intake.intake(conn, 'C3', {'expected': 5, 'batch': {
            'id': 'S9', 'kind': 'screen', 'cycle': 'K1', 'frame': {'offset': [0, 0], 'turn': 0, 'mirror': False},
            'csv': HEADER + '0,1,ok,10,60,\n'}}, write=False)
        raise AssertionError('位姿冲突应被拒')
    except DomainError as exc:
        assert exc.code == 'cycle_pose_conflict', exc.code
    # 对照：同一份标记证据，把测量按 0 度处理，重建东西两点及其他受影响的分选行
    # （此处东西两点互换），再重算包内摘要；核验仍须拒绝。
    forged = json.loads(json.dumps(release))
    forged['payload']['cycles'][0]['pose'] = {'turn': 0, 'mirror': False, 'offset': [0, 0]}
    swapped = {}
    for row in forged['payload']['rows']:
        site = (row['x'], row['y'])
        if site == (-1, 0):
            site = (1, 0)
        elif site == (1, 0):
            site = (-1, 0)
        swapped[site] = {**row, 'x': site[0], 'y': site[1]}
    forged['payload']['rows'] = [swapped[key] for key in sorted(swapped, key=lambda s: (s[1], s[0]))]
    from diegrade.codec import digest
    forged['sha256'] = digest(forged['payload'])
    try:
        archives.verify(forged)
        raise AssertionError('伪造 0 度应被拒')
    except DomainError as exc:
        assert exc.code == 'release_replay', exc.code
    # 同一份标记证据也不能被说成 0 度已确认；确认时的原始标记要求唯一 180 度。
    assert release['payload']['usage'], release['payload'].get('usage')
    return release, rows


with TemporaryDirectory() as temp:
    db = Database(Path(temp) / 'db.sqlite3')
    with db.transaction(write=True) as conn:
        release, rows = run(conn)
    print('场景通过：', rows)
