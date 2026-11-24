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
CREATE TABLE stations (
    scope TEXT NOT NULL DEFAULT '',
    id TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (scope, id)
);
CREATE TABLE work_orders (
    id TEXT PRIMARY KEY,
    wafer TEXT NOT NULL REFERENCES wafers(id),
    station_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'done', 'completed')),
    body TEXT NOT NULL,
    completion TEXT,
    result TEXT
);
CREATE TABLE order_sites (
    order_id TEXT NOT NULL REFERENCES work_orders(id),
    wafer TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    parent TEXT NOT NULL,
    is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
    PRIMARY KEY (order_id, x, y),
    UNIQUE (order_id, ordinal)
);
CREATE TABLE point_locks (
    wafer TEXT NOT NULL REFERENCES wafers(id),
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    order_id TEXT NOT NULL REFERENCES work_orders(id),
    PRIMARY KEY (wafer, x, y)
);
CREATE INDEX order_sites_wafer_idx ON order_sites(wafer, x, y);
PRAGMA user_version = 2;
'''

MIGRATION_2 = '''
CREATE TABLE IF NOT EXISTS stations (
    scope TEXT NOT NULL DEFAULT '',
    id TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (scope, id)
);
CREATE TABLE IF NOT EXISTS work_orders (
    id TEXT PRIMARY KEY,
    wafer TEXT NOT NULL REFERENCES wafers(id),
    station_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'done')),
    body TEXT NOT NULL,
    completion TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS order_sites (
    order_id TEXT NOT NULL REFERENCES work_orders(id),
    wafer TEXT NOT NULL,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    parent TEXT NOT NULL,
    is_open INTEGER NOT NULL CHECK (is_open IN (0, 1)),
    PRIMARY KEY (order_id, x, y),
    UNIQUE (order_id, ordinal)
);
CREATE TABLE IF NOT EXISTS point_locks (
    wafer TEXT NOT NULL REFERENCES wafers(id),
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    order_id TEXT NOT NULL REFERENCES work_orders(id),
    PRIMARY KEY (wafer, x, y)
);
CREATE INDEX IF NOT EXISTS order_sites_wafer_idx ON order_sites(wafer, x, y);
PRAGMA user_version = 2;
'''
