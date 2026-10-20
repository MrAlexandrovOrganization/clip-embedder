# server → Hostkey Russia + WSL

The coordinator and its SQLite queue run on hostkey-russia; CPU/GPU inference
runs on WSL (and the existing Mac worker when available). No server fallback
is defined in compose/server.yml. Worker TLS hostname and individual credentials
are preserved. compose/russia.yml binds consumer gRPC only to 100.64.0.5.

On the source, STACK_ROLE=forwarder selects compose/forwarder.yml and forwards
legacy Docker consumer RPCs to 100.64.0.5:50051. It has no model, queue or worker.
The original coordinator/worker are stopped with restart=no; volumes are retained.
A final cold SQLite snapshot must pass integrity_check and SHA-256 comparison.

Rollback: stop the destination first, copy its newest consistent SQLite queue
back, restore its credential registry, then reactivate the old source configuration.
Never run two coordinators on divergent copies of the queue. Runtime settings
and private backups are outside Git, under ~/.local/state/server-migration-20261020
on the administrative Mac, with copies on the destination hosts.
