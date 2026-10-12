SELECT COUNT(*) FROM jobs WHERE status='running' AND expires<=?;
