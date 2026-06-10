## ZeroMQ IPC (Core <-> UI)

This project uses a simple pattern:

- **PUB/SUB**: Core publishes status/events, UI subscribes.
- **REQ/REP**: UI sends commands, Core replies.

Wire format:

- UTF-8 JSON bytes for all messages.
- PUB payload: `{"topic":"channel.status","data":{...}}`
- REQ payload: `{"cmd":"resume_auto","args":{...},"request_id":"uuid"}`
- REP payload: `{"ok":true,"request_id":"uuid","result":{...}}`

Note: sockets, ports, and binding addresses are configured later.

