"""Session state management for the Aviary MCP server.

Each session holds an AviaryProblem instance, parameter state, mission config,
and run results. Sessions are stored in an in-memory dict keyed by UUID.
"""

import uuid
import time
import threading
import logging
from datetime import datetime, timezone

from design_space import DESIGN_PARAMETERS, VALID_PARAMETER_NAMES

logger = logging.getLogger(__name__)

# Default idle timeout: 30 minutes
SESSION_IDLE_TIMEOUT = 30 * 60


class AviarySession:
    """Holds state for one active Aviary session."""

    def __init__(self, session_id):
        self.session_id = session_id
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_accessed = time.time()

        # Current aircraft parameter overrides (PRD-style name -> float value)
        self.aircraft_params = {}

        # Mission configuration
        self.mission_config = {
            "range_nmi": 1500,
            "num_passengers": 162,
            "cruise_mach": 0.785,
            "cruise_altitude_ft": 35000,
            "optimizer_max_iter": 200,
        }

        # The AviaryProblem instance (created lazily at run time)
        self.prob = None

        # Last run results
        self.last_run_results = None
        self.last_run_converged = None
        self.last_run_exit_code = None

    def touch(self):
        """Update last-accessed timestamp."""
        self.last_accessed = time.time()

    def is_expired(self, timeout=SESSION_IDLE_TIMEOUT):
        """Check if session has exceeded idle timeout."""
        return (time.time() - self.last_accessed) > timeout


class SessionManager:
    """Manages the lifecycle of Aviary sessions."""

    def __init__(self, idle_timeout=SESSION_IDLE_TIMEOUT):
        self._sessions = {}
        self._lock = threading.Lock()
        self._idle_timeout = idle_timeout
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()

    def start_cleanup_thread(self):
        """Start the background session cleanup thread."""
        self._stop_cleanup.clear()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True
        )
        self._cleanup_thread.start()

    def stop_cleanup_thread(self):
        """Stop the background cleanup thread."""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)

    def _cleanup_loop(self):
        """Periodically check for and remove expired sessions."""
        while not self._stop_cleanup.is_set():
            self._stop_cleanup.wait(timeout=60)  # Check every 60 seconds
            if self._stop_cleanup.is_set():
                break
            self._cleanup_expired()

    def _cleanup_expired(self):
        """Remove all expired sessions."""
        with self._lock:
            expired = [
                sid for sid, session in self._sessions.items()
                if session.is_expired(self._idle_timeout)
            ]
            for sid in expired:
                logger.info("Cleaning up expired session: %s", sid)
                del self._sessions[sid]

    def create_session(self):
        """Create a new session and return it."""
        session_id = str(uuid.uuid4())
        session = AviarySession(session_id)
        with self._lock:
            self._sessions[session_id] = session
        logger.info("Created session: %s", session_id)
        return session

    def get_session(self, session_id):
        """Retrieve a session by ID. Returns None if not found or expired."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired(self._idle_timeout):
                del self._sessions[session_id]
                return None
            session.touch()
            return session

    def remove_session(self, session_id):
        """Explicitly remove a session."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def active_session_count(self):
        """Return the number of active (non-expired) sessions."""
        with self._lock:
            return len(self._sessions)
