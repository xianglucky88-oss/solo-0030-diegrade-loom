VERSION = 2

SQL = '''
CREATE TABLE rules (
    name TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (name, version)
);
CREATE TABLE wafers (
    id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE events (
    wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('batch', 'hold', 'correction')),
    ref TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision), UNIQUE (wafer, kind, ref)
);
CREATE TABLE releases (
    wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision)
);
PRAGMA user_version = 2;
'''

# 旧库只放宽事件类型的 CHECK，现场记录逐条保留。
MIGRATIONS = {
    1: '''
    CREATE TABLE events_v2 (
        wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('batch', 'hold', 'correction')),
        ref TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY (wafer, revision), UNIQUE (wafer, kind, ref)
    );
    INSERT INTO events_v2 SELECT wafer, revision, kind, ref, body FROM events;
    DROP TABLE events;
    ALTER TABLE events_v2 RENAME TO events;
    PRAGMA user_version = 2;
    ''',
}
