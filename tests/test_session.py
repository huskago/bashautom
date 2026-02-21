import time
import signal
import pytest
from bashautom import Session, SessionManager
from bashautom.session import StreamEvent


class TestSession:
    def test_simple_command(self):
        with Session() as s:
            r = s.execute("echo hello")
            assert r.stdout == "hello"
            assert r.exit_code == 0
            assert r.success

    def test_exit_code(self):
        with Session() as s:
            r = s.execute("(exit 42)")
            assert r.exit_code == 42
            assert not r.success

    def test_failed_command(self):
        with Session() as s:
            r = s.execute("ls /nonexistent_path_12345")
            assert not r.success
            assert r.exit_code != 0
            assert r.stderr

    def test_stderr(self):
        with Session() as s:
            r = s.execute("echo err >&2")
            assert "err" in r.stderr

    def test_state_persists(self):
        with Session() as s:
            s.execute("export FOO=bar")
            r = s.execute("echo $FOO")
            assert r.stdout == "bar"

    def test_cwd_persists(self):
        with Session() as s:
            s.execute("cd /tmp")
            r = s.execute("pwd")
            assert r.stdout == "/tmp"

    def test_multiline_output(self):
        with Session() as s:
            r = s.execute("echo -e 'a\\nb\\nc'")
            lines = r.stdout.strip().splitlines()
            assert len(lines) == 3

    def test_empty_output(self):
        with Session() as s:
            r = s.execute("true")
            assert r.stdout == ""
            assert r.success

    def test_duration_tracked(self):
        with Session() as s:
            r = s.execute("sleep 0.2")
            assert r.duration >= 0.15

    def test_special_chars(self):
        with Session() as s:
            r = s.execute("echo 'hello \"world\" $HOME'")
            assert "hello" in r.stdout
            assert "$HOME" not in r.stdout or "hello" in r.stdout


class TestTimeout:
    def test_timeout_kills_command(self):
        with Session() as s:
            r = s.execute("sleep 30", timeout=1)
            assert r.timed_out
            assert not r.success
            assert r.duration < 5

    def test_session_survives_timeout(self):
        with Session() as s:
            s.execute("sleep 30", timeout=1)
            r = s.execute("echo alive")
            assert r.stdout == "alive"
            assert r.success

    def test_no_timeout_if_fast(self):
        with Session() as s:
            r = s.execute("echo fast", timeout=10)
            assert not r.timed_out
            assert r.success


class TestStreaming:
    def test_stream_callback(self):
        chunks = []

        def cb(event: StreamEvent):
            chunks.append(event)

        with Session() as s:
            s.execute("echo one; echo two; echo three", stream_callback=cb)

        stdout_data = "".join(e.data for e in chunks if e.stream == "stdout")
        assert "one" in stdout_data
        assert "two" in stdout_data
        assert "three" in stdout_data

    def test_stream_event_fields(self):
        events = []

        def cb(event: StreamEvent):
            events.append(event)

        with Session() as s:
            s.execute("echo test", stream_callback=cb)

        stdout_events = [e for e in events if e.stream == "stdout"]
        assert len(stdout_events) > 0
        assert stdout_events[0].timestamp > 0


class TestEnvHelpers:
    def test_set_get_env(self):
        with Session() as s:
            s.set_env("TEST_VAR", "hello123")
            assert s.get_env("TEST_VAR") == "hello123"

    def test_get_env_unset(self):
        with Session() as s:
            val = s.get_env("DOESNT_EXIST_SURELY_12345")
            assert val is None

    def test_get_cwd(self):
        with Session() as s:
            s.execute("cd /tmp")
            assert s.get_cwd() == "/tmp"


class TestSessionLifecycle:
    def test_pid(self):
        with Session() as s:
            assert isinstance(s.pid, int)
            assert s.pid > 0

    def test_alive(self):
        s = Session()
        assert s.alive
        s.close()
        assert not s.alive

    def test_double_close(self):
        s = Session()
        s.close()
        s.close()  # should not raise

    def test_context_manager(self):
        with Session() as s:
            assert s.alive
        assert not s.alive

    def test_name_default(self):
        with Session() as s:
            assert s.name.startswith("session-")

    def test_name_custom(self):
        with Session(name="mytest") as s:
            assert s.name == "mytest"

    def test_execute_after_close_raises(self):
        s = Session()
        s.close()
        with pytest.raises(Exception):
            s.execute("echo nope")


class TestSessionManager:
    def test_create_and_get(self):
        with SessionManager() as mgr:
            mgr.create("a")
            s = mgr.get("a")
            assert s.alive

    def test_create_duplicate_raises(self):
        with SessionManager() as mgr:
            mgr.create("x")
            with pytest.raises(ValueError):
                mgr.create("x")

    def test_get_missing_raises(self):
        with SessionManager() as mgr:
            with pytest.raises(KeyError):
                mgr.get("nope")

    def test_get_or_create(self):
        with SessionManager() as mgr:
            s1 = mgr.get_or_create("w")
            s2 = mgr.get_or_create("w")
            assert s1 is s2

    def test_close_one(self):
        with SessionManager() as mgr:
            mgr.create("a")
            mgr.create("b")
            mgr.close("a")
            assert "a" not in mgr
            assert "b" in mgr

    def test_names(self):
        with SessionManager() as mgr:
            mgr.create("x")
            mgr.create("y")
            assert set(mgr.names) == {"x", "y"}

    def test_active(self):
        with SessionManager() as mgr:
            mgr.create("a")
            mgr.create("b")
            assert len(mgr.active) == 2

    def test_close_all(self):
        with SessionManager() as mgr:
            mgr.create("a")
            mgr.create("b")
            mgr.close_all()
            assert len(mgr) == 0

    def test_contains(self):
        with SessionManager() as mgr:
            mgr.create("test")
            assert "test" in mgr
            assert "nope" not in mgr

    def test_getitem(self):
        with SessionManager() as mgr:
            mgr.create("s")
            assert mgr["s"].alive

    def test_isolation(self):
        with SessionManager() as mgr:
            a = mgr.create("a")
            b = mgr.create("b")
            a.execute("export X=fromA")
            b.execute("export X=fromB")
            assert a.execute("echo $X").stdout == "fromA"
            assert b.execute("echo $X").stdout == "fromB"
