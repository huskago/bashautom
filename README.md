# bashautom

Persistent bash sessions for Python.

Unlike `subprocess.run()` which spawns a new process every time, bashautom keeps a `/bin/bash` process alive so state (env vars, cwd, etc.) persists across commands.

```python
from bashautom import Session

with Session() as s:
    s.execute("cd /opt/myproject")
    s.execute("source .env")
    s.execute("export BUILD_ID=42")
    result = s.execute("make build")
```

## Install

```bash
pip install bashautom
```

Python 3.10+, Linux/macOS only.

## Usage

```python
from bashautom import Session

with Session() as s:
    result = s.execute("echo hello")
    print(result.stdout)
    print(result.exit_code)
    print(result.success)
```

### Timeouts

Commands can be killed without destroying the session:

```python
with Session() as s:
    result = s.execute("sleep 60", timeout=3)
    print(result.timed_out)

    # session still works
    s.execute("echo ok")
```

### Streaming

```python
from bashautom.session import StreamEvent

def on_output(event: StreamEvent):
    print(f"[{event.stream}] {event.data.strip()}")

with Session() as s:
    s.execute("for i in 1 2 3; do echo $i; sleep 0.5; done", stream_callback=on_output)
```

### Multiple sessions

```python
from bashautom import SessionManager

with SessionManager() as mgr:
    build = mgr.create("build", cwd="/opt/project")
    deploy = mgr.create("deploy", cwd="/opt/infra")

    build.execute("make release")
    deploy.execute("./deploy.sh")
```

### Env helpers

```python
with Session() as s:
    s.set_env("PROJECT", "bashautom")
    print(s.get_env("PROJECT"))
    print(s.get_cwd())
    print(s.pid)
    print(s.alive)
```

## API

### Session

- `execute(command, timeout=None, stream_callback=None)` - run a command, returns `CommandResult`
- `send_signal(sig=SIGINT)` - send a signal to the running process
- `get_cwd()` / `get_env(var)` / `set_env(var, value)` - shell state access
- `close()` - kill the session
- `pid`, `alive` - process info

### CommandResult

- `command`, `stdout`, `stderr` - what ran and what came back
- `exit_code`, `success`, `timed_out` - status
- `duration` - wall time in seconds

### SessionManager

- `create(name, ...)` / `get(name)` / `get_or_create(name, ...)` - session lifecycle
- `close(name)` / `close_all()` - cleanup
- `names`, `active` - introspection

## License

MIT