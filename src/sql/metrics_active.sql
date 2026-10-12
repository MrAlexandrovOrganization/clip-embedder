SELECT worker,COUNT(*),COALESCE(MAX(?-attempt_started),0) FROM jobs WHERE status='running' AND expires>? GROUP BY worker;
