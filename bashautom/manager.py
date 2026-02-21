from typing import Optional
from .session import Session


class SessionManager:
    """Named session pool. Create, get, and cleanup multiple sessions."""
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(
        self,
        name: str,
        shell: str = "/bin/bash",
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> Session:
        """Create a named session. Raises KeyError if name exists."""
        if name in self._sessions:
            raise ValueError(f"Session '{name}' already exists. Use get() or close it first.")

        session = Session(shell=shell, env=env, cwd=cwd, name=name)
        self._sessions[name] = session
        return session

    def get(self, name: str) -> Session:
        """Get session by name. Raises KeyError if not found."""
        if name not in self._sessions:
            raise KeyError(f"No session named '{name}'. Available: {list(self._sessions.keys())}")
        return self._sessions[name]

    def get_or_create(
        self,
        name: str,
        shell: str = "/bin/bash",
        env: Optional[dict] = None,
        cwd: Optional[str] = None,
    ) -> Session:
        """Get or create a session."""
        if name in self._sessions and self._sessions[name].alive:
            return self._sessions[name]
        # Clean up dead session if it exists
        if name in self._sessions:
            self._sessions[name].close()
            del self._sessions[name]
        return self.create(name, shell=shell, env=env, cwd=cwd)

    def close(self, name: str) -> None:
        """Close and remove a specific session."""
        if name in self._sessions:
            self._sessions[name].close()
            del self._sessions[name]

    def close_all(self) -> None:
        """Close everything."""
        for session in self._sessions.values():
            try:
                session.close()
            except Exception:
                pass
        self._sessions.clear()

    @property
    def names(self) -> list[str]:
        """List all session names."""
        return list(self._sessions.keys())

    @property
    def active(self) -> list[Session]:
        """List all alive sessions."""
        return [s for s in self._sessions.values() if s.alive]

    def __contains__(self, name: str) -> bool:
        return name in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)

    def __getitem__(self, name: str) -> Session:
        return self.get(name)

    def __enter__(self) -> "SessionManager":
        return self

    def __exit__(self, *args) -> None:
        self.close_all()

    def __repr__(self) -> str:
        alive = len(self.active)
        total = len(self._sessions)
        return f"<SessionManager sessions={total} alive={alive}>"