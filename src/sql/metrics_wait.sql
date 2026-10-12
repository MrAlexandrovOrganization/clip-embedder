SELECT COALESCE(MAX(?-created),0) FROM jobs WHERE status='queued' OR (status='running' AND expires<=?);
