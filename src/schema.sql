CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload BLOB NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', result BLOB,
 created REAL NOT NULL, accessed REAL NOT NULL, available REAL NOT NULL,
 worker TEXT, instance TEXT, lease TEXT, expires REAL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, available, created);
CREATE TABLE IF NOT EXISTS workers (
 worker TEXT NOT NULL, instance TEXT NOT NULL, ready INTEGER NOT NULL,
 model TEXT NOT NULL, pretrained TEXT NOT NULL, reported REAL NOT NULL,
 PRIMARY KEY(worker,instance)
);
