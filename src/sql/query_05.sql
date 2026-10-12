INSERT INTO workers VALUES(?,?,?,?,?,?) ON CONFLICT(worker,instance) DO UPDATE SET ready=excluded.ready,model=excluded.model,pretrained=excluded.pretrained,reported=excluded.reported;
