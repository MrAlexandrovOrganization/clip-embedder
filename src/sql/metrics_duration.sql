SELECT worker,finished-attempt_started,finished-created FROM jobs WHERE status='done' AND finished>? AND attempt_started IS NOT NULL;
