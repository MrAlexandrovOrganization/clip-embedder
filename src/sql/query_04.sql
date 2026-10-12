DELETE FROM jobs WHERE status IN ('done','failed') AND accessed < ?;
