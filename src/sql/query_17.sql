UPDATE jobs SET status=?,available=?,payload=CASE WHEN ? THEN X'' ELSE payload END WHERE id=?;
