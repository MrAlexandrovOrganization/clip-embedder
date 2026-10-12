SELECT worker,MAX(ready AND reported>? AND model=? AND pretrained=?),?-MAX(reported),SUM(ready AND reported>? AND model=? AND pretrained=?) FROM workers GROUP BY worker;
