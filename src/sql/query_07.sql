UPDATE jobs SET status='running',worker=?,instance=?,lease=?,expires=?,attempts=attempts+1 WHERE id=?;
