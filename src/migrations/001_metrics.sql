ALTER TABLE jobs ADD COLUMN attempt_started REAL;
ALTER TABLE jobs ADD COLUMN finished REAL;
CREATE INDEX jobs_finished ON jobs(finished) WHERE finished IS NOT NULL;
CREATE TABLE compute_counters (
 worker TEXT NOT NULL, event TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(worker,event),
 CHECK(event IN ('completed','failed','attempt_failed','retry','lease_expired'))
);
CREATE TRIGGER jobs_metric_timestamps AFTER UPDATE ON jobs
WHEN (NEW.status='running' AND NEW.lease IS NOT OLD.lease)
 OR (NEW.status IN ('done','failed') AND NEW.status<>OLD.status)
BEGIN
 UPDATE jobs SET
 attempt_started=CASE WHEN NEW.status='running' THEN (julianday('now')-2440587.5)*86400 ELSE attempt_started END,
 finished=CASE WHEN NEW.status IN ('done','failed') THEN (julianday('now')-2440587.5)*86400 ELSE NULL END
 WHERE id=NEW.id;
END;
CREATE TRIGGER jobs_metric_counters AFTER UPDATE ON jobs
BEGIN
 INSERT INTO compute_counters(worker,event,value) SELECT COALESCE(NEW.worker,'other'),'completed',1 WHERE NEW.status='done' AND OLD.status<>'done'
 ON CONFLICT(worker,event) DO UPDATE SET value=compute_counters.value+1;
 INSERT INTO compute_counters(worker,event,value) SELECT COALESCE(NEW.worker,'other'),'failed',1 WHERE NEW.status='failed' AND OLD.status<>'failed'
 ON CONFLICT(worker,event) DO UPDATE SET value=compute_counters.value+1;
 INSERT INTO compute_counters(worker,event,value) SELECT COALESCE(NEW.worker,'other'),'attempt_failed',1 WHERE OLD.status='running' AND NEW.status IN ('queued','failed') AND OLD.expires>(julianday('now')-2440587.5)*86400
 ON CONFLICT(worker,event) DO UPDATE SET value=compute_counters.value+1;
 INSERT INTO compute_counters(worker,event,value) SELECT COALESCE(NEW.worker,'other'),'retry',1 WHERE NEW.status='running' AND NEW.lease IS NOT OLD.lease AND NEW.attempts>1
 ON CONFLICT(worker,event) DO UPDATE SET value=compute_counters.value+1;
 INSERT INTO compute_counters(worker,event,value) SELECT COALESCE(OLD.worker,'other'),'lease_expired',1 WHERE OLD.status='running' AND ((NEW.status='running' AND NEW.lease IS NOT OLD.lease) OR (NEW.status='failed' AND OLD.expires<=(julianday('now')-2440587.5)*86400))
 ON CONFLICT(worker,event) DO UPDATE SET value=compute_counters.value+1;
END;
