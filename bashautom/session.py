import os
import subprocess
import selectors
import time
import secrets
import signal
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class CommandResult:
    """Output of Session.execute()."""
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAIL({self.exit_code})"
        return f"<CommandResult [{status}] {self.command!r} ({self.duration:.2f}s)>"


@dataclass
class StreamEvent:
    """Chunk of output from a streaming execute() call."""
    stream: str          # "stdout" or "stderr"
    data: str            # the chunk of text
    timestamp: float     # time.monotonic()


class SessionError(Exception):
    """Raised when the session is in an invalid state."""
    pass


class Session:
    """Persistent bash session. State carries over between execute() calls."""
    _TOKEN_PREFIX = "__BASHAUTOM_END_krjyngsczkvmlzqaoxpgudjhkejvbowc__"

    def __init__(
        self,
        shell: str = "/bin/bash",
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
        name: Optional[str] = None,
    ):
        self.shell = shell
        self.name = name or f"session-{secrets.token_hex(4)}"
        self._closed = False

        # Spawn the bash process (non-interactive to avoid input echo on stderr)
        self._proc = subprocess.Popen(
            [shell, "--norc", "--noprofile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            # Ensure line-buffered isn't forced; we handle our own buffering
            bufsize=0,
            # New process group so we can signal children
            preexec_fn=os.setsid,
        )

        # Setup selector for non-blocking reads on stdout and stderr
        self._sel = selectors.DefaultSelector()
        os.set_blocking(self._proc.stdout.fileno(), False)
        os.set_blocking(self._proc.stderr.fileno(), False)
        self._sel.register(self._proc.stdout, selectors.EVENT_READ, "stdout")
        self._sel.register(self._proc.stderr, selectors.EVENT_READ, "stderr")

        self._drain(timeout=0.5)

        # Setup: trap SIGINT in bash with a no-op handler so that:
        # - bash itself survives SIGINT (doesn't exit)
        # - child processes still get default SIGINT behavior (they die)
        # This is crucial for timeout support, we SIGINT the process group,
        # which kills the running child but keeps bash alive for the next command.
        self._proc.stdin.write(b"trap : INT\n")
        self._proc.stdin.flush()
        self._drain(timeout=0.2)

    def _generate_token(self) -> str:
        """Generate a unique end-of-command token."""
        return f"{self._TOKEN_PREFIX}{secrets.token_hex(8)}"

    def _drain(self, timeout: float = 0.1) -> tuple[str, str]:
        """Drain all pending output from stdout/stderr."""
        stdout_buf = []
        stderr_buf = []
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            # FIXME: _drain can miss output if process writes in bursts > 50ms apart
            events = self._sel.select(timeout=min(remaining, 0.05))
            if not events:
                break

            for key, _ in events:
                chunk = key.fileobj.read(65536)
                if chunk:
                    text = chunk.decode("utf-8", errors="replace")
                    if key.data == "stdout":
                        stdout_buf.append(text)
                    else:
                        stderr_buf.append(text)

        return "".join(stdout_buf), "".join(stderr_buf)

    def _ensure_alive(self) -> None:
        """Raise if the session or underlying process is dead."""
        if self._closed:
            raise SessionError(f"Session '{self.name}' is closed.")
        if self._proc.poll() is not None:
            self._closed = True
            raise SessionError(
                f"Session '{self.name}' process exited with code {self._proc.returncode}."
            )

    def execute(self, command: str, timeout: Optional[float] = None, 
            stream_callback: Optional[Callable] = None) -> CommandResult:
        """Run a command. Returns CommandResult.
        
        timeout kills the command but keeps the session alive.
        stream_callback gets StreamEvent objects as output arrives.
        """

        self._ensure_alive()

        token = self._generate_token()
        start = time.monotonic()

        # Build the payload
        payload = (
            f"{command}\n"
            f"__bashautom_ec=$?\n"
            f"echo \"{token}:$__bashautom_ec\"\n"
        )
        self._proc.stdin.write(payload.encode("utf-8"))
        self._proc.stdin.flush()

        # Collect output until we see the token
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        found_token = False
        exit_code = -1
        timed_out = False

        while not found_token:
            elapsed = time.monotonic() - start

            if timeout is not None and elapsed >= timeout and not timed_out:
                timed_out = True
                # Send SIGINT to the process group , kills the child command,
                # but bash survives thanks to `trap : INT`
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGINT)
                except ProcessLookupError:
                    pass
                # Don't break , continue reading to find the token
                # (bash will proceed to echo it after the child dies)
                # Extend the deadline so we can capture the token
                timeout = elapsed + 3.0
                continue

            if timed_out and timeout is not None and elapsed >= timeout:
                # Extended grace period also expired , bail out
                break

            sel_timeout = None
            if timeout is not None:
                sel_timeout = max(0.01, timeout - elapsed)

            events = self._sel.select(timeout=min(sel_timeout or 1.0, 1.0))

            for key, _ in events:
                chunk = key.fileobj.read(65536)
                if not chunk:
                    continue

                text = chunk.decode("utf-8", errors="replace")
                stream_name = key.data

                if stream_name == "stdout":
                    # Check if the token is in this chunk
                    if token in text:
                        # Split: everything before the token line is real output
                        lines = text.split("\n")
                        clean_lines = []
                        for line in lines:
                            if token in line:
                                # Parse exit code from "TOKEN:CODE"
                                try:
                                    exit_code = int(line.split(":")[-1].strip())
                                except (ValueError, IndexError):
                                    exit_code = -1
                                found_token = True
                            else:
                                clean_lines.append(line)
                        clean_text = "\n".join(clean_lines)
                        if clean_text.strip() and stream_callback:
                            stream_callback(StreamEvent(
                                stream=stream_name,
                                data=clean_text,
                                timestamp=time.monotonic(),
                            ))
                        if clean_lines:
                            stdout_chunks.append(clean_text)
                    else:
                        if stream_callback:
                            stream_callback(StreamEvent(
                                stream=stream_name,
                                data=text,
                                timestamp=time.monotonic(),
                            ))
                        stdout_chunks.append(text)
                else:
                    if stream_callback:
                        stream_callback(StreamEvent(
                            stream=stream_name,
                            data=text,
                            timestamp=time.monotonic(),
                        ))
                    stderr_chunks.append(text)

            # Check if process died
            if self._proc.poll() is not None and not found_token:
                self._closed = True
                break

        duration = time.monotonic() - start

        stdout_text = "".join(stdout_chunks).strip()
        stderr_text = "".join(stderr_chunks).strip()

        # Clean up: remove the echo of our payload commands from stdout
        # (bash -i echoes input lines back to stdout)
        for noise in [
            f"__bashautom_ec=$?",
            f'echo "{token}:$__bashautom_ec"',
        ]:
            stdout_text = stdout_text.replace(noise, "")
        stdout_text = stdout_text.strip()

        return CommandResult(
            command=command,
            stdout=stdout_text,
            stderr=stderr_text,
            exit_code=exit_code,
            duration=duration,
            timed_out=timed_out,
        )

    def send_signal(self, sig: int = signal.SIGINT) -> None:
        """Send a signal to the process group."""
        self._ensure_alive()
        try:
            os.killpg(os.getpgid(self._proc.pid), sig)
        except ProcessLookupError:
            pass

    def get_cwd(self) -> str:
        """Current working directory."""
        result = self.execute("pwd")
        return result.stdout.strip()

    def get_env(self, var: str) -> Optional[str]:
        """Read an env var from the session."""
        result = self.execute(f"echo \"${{{var}}}\"")
        val = result.stdout.strip()
        return val if val else None

    # TODO: validate var name in set_env to prevent injection
    def set_env(self, var: str, value: str) -> None:
        """Export an env var."""
        self.execute(f"export {var}={value!r}")

    @property
    def pid(self) -> int:
        """PID of the underlying bash process."""
        return self._proc.pid

    @property
    def alive(self) -> bool:
        """True if the session process is still running."""
        return not self._closed and self._proc.poll() is None

    def close(self) -> None:
        """Kill the bash process and cleanup."""
        if self._closed:
            return
        self._closed = True

        try:
            self._sel.unregister(self._proc.stdout)
            self._sel.unregister(self._proc.stderr)
        except Exception:
            pass
        self._sel.close()

        try:
            self._proc.stdin.write(b"exit\n")
            self._proc.stdin.flush()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
            self._proc.wait()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        status = "alive" if self.alive else "closed"
        return f"<Session '{self.name}' [{status}] pid={self._proc.pid}>"