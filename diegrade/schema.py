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
PRAGMA user_version = 1;
'''

# 试评域与生产现场隔离：只读取已封存签出件的快照，不回写现场事件或晶圆代次。
TRIAL_SQL = '''
CREATE TABLE trials (
    wafer TEXT NOT NULL, trial TEXT NOT NULL,
    release_revision INTEGER NOT NULL, rule_name TEXT NOT NULL, rule_version INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    PRIMARY KEY (wafer, trial)
);
CREATE TABLE trial_orders (
    wafer TEXT NOT NULL, trial TEXT NOT NULL, seq INTEGER NOT NULL,
    site TEXT NOT NULL, reason_code TEXT NOT NULL, reason TEXT NOT NULL,
    first_ref TEXT NOT NULL, open INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (wafer, trial, seq),
    UNIQUE (wafer, trial, site)
);
CREATE TABLE trial_batches (
    wafer TEXT NOT NULL, trial TEXT NOT NULL, batch_id TEXT NOT NULL,
    frame TEXT NOT NULL, body TEXT NOT NULL,
    PRIMARY KEY (wafer, trial, batch_id)
);
'''
