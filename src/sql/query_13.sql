SELECT 1 FROM jobs WHERE status='running' AND worker=? AND instance=? AND expires>?;
