SELECT * FROM jobs WHERE (status='queued' AND available<=?) OR (status='running' AND expires<=? AND attempts<5) ORDER BY created LIMIT 1;
