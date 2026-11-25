VERSION = 2

EVENTS = '''
CREATE TABLE events (
    wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('batch', 'hold', 'round', 'submission', 'verdict', 'void')),
    ref TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision), UNIQUE (wafer, kind, ref)
);'''

SQL = '''
CREATE TABLE rules (
    name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (name, version)
);
CREATE TABLE wafers (
    id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
);
''' + EVENTS + '''
CREATE TABLE releases (
    wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision)
);
PRAGMA user_version = 2;
'''

# 第 1 版的事件表只允许首测/复测批次与复核两种记录，升级时原样搬迁已有现场。
MIGRATIONS = {
    1: '''
ALTER TABLE events RENAME TO events_v1;
''' + EVENTS + '''
INSERT INTO events (wafer, revision, kind, ref, body)
    SELECT wafer, revision, kind, ref, body FROM events_v1;
DROP TABLE events_v1;
PRAGMA user_version = 2;
''',
}
