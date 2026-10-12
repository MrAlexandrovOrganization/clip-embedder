UPDATE jobs SET status='failed',payload=X'' WHERE status='running' AND expires<=? AND attempts>=5;
