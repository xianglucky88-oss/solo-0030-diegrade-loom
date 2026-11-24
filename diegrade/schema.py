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
    kind TEXT NOT NULL CHECK (kind IN ('batch', 'hold')),
    ref TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision), UNIQUE (wafer, kind, ref)
);
CREATE TABLE releases (
    wafer TEXT NOT NULL REFERENCES wafers(id), revision INTEGER NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, revision)
);
CREATE TABLE zone_plans (
    wafer TEXT NOT NULL REFERENCES wafers(id), zone TEXT NOT NULL, seq INTEGER NOT NULL,
    sha256 TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, zone, seq), UNIQUE (wafer, zone, sha256)
);
CREATE TABLE zone_releases (
    wafer TEXT NOT NULL REFERENCES wafers(id), zone TEXT NOT NULL,
    sha256 TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, zone)
);
PRAGMA user_version = 2;
'''

MIGRATE_1_TO_2 = '''
CREATE TABLE zone_plans (
    wafer TEXT NOT NULL REFERENCES wafers(id), zone TEXT NOT NULL, seq INTEGER NOT NULL,
    sha256 TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, zone, seq), UNIQUE (wafer, zone, sha256)
);
CREATE TABLE zone_releases (
    wafer TEXT NOT NULL REFERENCES wafers(id), zone TEXT NOT NULL,
    sha256 TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, zone)
);
PRAGMA user_version = 2;
'''
