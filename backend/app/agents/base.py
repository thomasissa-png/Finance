"""Base agent class + message bus + structured logging.

Every agent inherits from BaseAgent and gets:
- publish/consume on a shared message bus (PostgreSQL or in-memory fallback)
- structured logging visible in the frontend
- status tracking (idle / working / error)
- duration tracking per action
"""

import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


# ── Agent Status ───────────────────────────────────────────────────────

class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    ERROR = "error"
    DISABLED = "disabled"


# ── Message Bus ────────────────────────────────────────────────────────

class MessageBus:
    """Inter-agent communication via PostgreSQL (primary) or in-memory (fallback).

    Messages are typed (msg_type) and optionally targeted (to_agent).
    Agents consume messages by type — once consumed, they're marked done.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._memory_messages: deque = deque(maxlen=1000)
        self._memory_lock = threading.Lock()
        self._subscribers: dict[str, list] = {}  # msg_type -> [callback]

    def _use_pg(self) -> bool:
        try:
            from ..database import is_pg_enabled
            return is_pg_enabled()
        except Exception:
            return False

    def publish(self, from_agent: str, msg_type: str, payload: dict,
                to_agent: str | None = None) -> None:
        """Publish a message to the bus."""
        msg = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_agent": from_agent,
            "to_agent": to_agent,
            "msg_type": msg_type,
            "payload": payload,
            "consumed": False,
        }

        if self._use_pg():
            try:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO agent_messages (from_agent, to_agent, msg_type, payload)
                            VALUES (%s, %s, %s, %s)
                        """, (from_agent, to_agent, msg_type, json.dumps(payload)))
            except Exception as exc:
                logger.warning("PG publish failed, using memory: %s", exc)
                with self._memory_lock:
                    self._memory_messages.append(msg)
        else:
            with self._memory_lock:
                self._memory_messages.append(msg)

        # Notify subscribers
        for callback in self._subscribers.get(msg_type, []):
            try:
                callback(msg)
            except Exception as exc:
                logger.error("Subscriber callback error for %s: %s", msg_type, exc)

    def consume(self, agent_name: str, msg_types: list[str],
                limit: int = 50) -> list[dict]:
        """Consume unread messages for this agent by type."""
        if self._use_pg():
            try:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        # Fetch messages targeted to this agent or broadcast
                        cur.execute("""
                            UPDATE agent_messages
                            SET consumed = TRUE
                            WHERE consumed = FALSE
                              AND msg_type = ANY(%s)
                              AND (to_agent IS NULL OR to_agent = %s)
                            RETURNING id, timestamp, from_agent, to_agent, msg_type, payload
                            ORDER BY timestamp ASC
                            LIMIT %s
                        """, (msg_types, agent_name, limit))
                        # Note: RETURNING with ORDER BY may not be supported everywhere
                        # but works in PostgreSQL
                        rows = cur.fetchall()
                        return [
                            {
                                "id": r[0],
                                "timestamp": r[1].isoformat() if r[1] else None,
                                "from_agent": r[2],
                                "to_agent": r[3],
                                "msg_type": r[4],
                                "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5]),
                            }
                            for r in rows
                        ]
            except Exception as exc:
                logger.warning("PG consume failed, using memory: %s", exc)

        # In-memory fallback
        results = []
        with self._memory_lock:
            for msg in self._memory_messages:
                if (not msg["consumed"]
                        and msg["msg_type"] in msg_types
                        and (msg["to_agent"] is None or msg["to_agent"] == agent_name)):
                    msg["consumed"] = True
                    results.append(msg)
                    if len(results) >= limit:
                        break
        return results

    def subscribe(self, msg_type: str, callback) -> None:
        """Register a callback for a message type (real-time notification)."""
        self._subscribers.setdefault(msg_type, []).append(callback)

    def recent_messages(self, limit: int = 50) -> list[dict]:
        """Get recent messages for debugging (all types)."""
        if self._use_pg():
            try:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT id, timestamp, from_agent, to_agent, msg_type,
                                   payload, consumed
                            FROM agent_messages
                            ORDER BY timestamp DESC
                            LIMIT %s
                        """, (limit,))
                        rows = cur.fetchall()
                        return [
                            {
                                "id": r[0],
                                "timestamp": r[1].isoformat() if r[1] else None,
                                "from_agent": r[2],
                                "to_agent": r[3],
                                "msg_type": r[4],
                                "payload": r[5] if isinstance(r[5], dict) else json.loads(r[5]),
                                "consumed": r[6],
                            }
                            for r in rows
                        ]
            except Exception:
                pass

        with self._memory_lock:
            return list(reversed(list(self._memory_messages)))[:limit]


# ── Agent Logger ───────────────────────────────────────────────────────

class AgentLogger:
    """Structured logging for agents — stored in PG or memory for frontend display."""

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._memory_logs: deque = deque(maxlen=500)
        self._lock = threading.Lock()

    def _use_pg(self) -> bool:
        try:
            from ..database import is_pg_enabled
            return is_pg_enabled()
        except Exception:
            return False

    def log(self, action: str, details: dict | None = None,
            level: str = "INFO", duration_ms: int | None = None) -> None:
        """Log a structured action."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_name": self.agent_name,
            "level": level,
            "action": action,
            "details": details or {},
            "duration_ms": duration_ms,
        }

        # Always log to Python logger too
        py_level = getattr(logging, level.upper(), logging.INFO)
        detail_str = f" | {json.dumps(details)}" if details else ""
        dur_str = f" ({duration_ms}ms)" if duration_ms else ""
        logger.log(py_level, "[%s] %s%s%s", self.agent_name, action, detail_str, dur_str)

        if self._use_pg():
            try:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO agent_logs
                                (agent_name, level, action, details, duration_ms)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (self.agent_name, level, action,
                              json.dumps(details) if details else None,
                              duration_ms))
            except Exception as exc:
                logger.warning("PG agent log failed: %s", exc)
                with self._lock:
                    self._memory_logs.append(entry)
        else:
            with self._lock:
                self._memory_logs.append(entry)

    def get_logs(self, limit: int = 50, level: str | None = None) -> list[dict]:
        """Retrieve recent logs for this agent."""
        if self._use_pg():
            try:
                from ..database import get_conn
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        if level:
                            cur.execute("""
                                SELECT timestamp, level, action, details, duration_ms
                                FROM agent_logs
                                WHERE agent_name = %s AND level = %s
                                ORDER BY timestamp DESC
                                LIMIT %s
                            """, (self.agent_name, level, limit))
                        else:
                            cur.execute("""
                                SELECT timestamp, level, action, details, duration_ms
                                FROM agent_logs
                                WHERE agent_name = %s
                                ORDER BY timestamp DESC
                                LIMIT %s
                            """, (self.agent_name, limit))
                        rows = cur.fetchall()
                        return [
                            {
                                "timestamp": r[0].isoformat() if r[0] else None,
                                "level": r[1],
                                "action": r[2],
                                "details": r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {}),
                                "duration_ms": r[4],
                            }
                            for r in reversed(rows)  # chronological order
                        ]
            except Exception:
                pass

        with self._lock:
            logs = list(self._memory_logs)
            if level:
                logs = [l for l in logs if l["level"] == level]
            return logs[-limit:]


# ── Base Agent ─────────────────────────────────────────────────────────

class BaseAgent:
    """Base class for all trading agents.

    Provides:
    - Structured logging (visible in frontend)
    - Message bus publish/consume
    - Status tracking
    - Duration measurement
    """

    name: str = "base"
    description: str = "Base agent"

    def __init__(self):
        self._status = AgentStatus.IDLE
        self._last_action: str | None = None
        self._last_action_time: str | None = None
        self._last_error: str | None = None
        self._action_count: int = 0
        self._lock = threading.Lock()
        self.bus = MessageBus()
        self.logger = AgentLogger(self.name)

    # ── Status ─────────────────────────────────────────────────────

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "description": self.description,
                "status": self._status.value,
                "last_action": self._last_action,
                "last_action_time": self._last_action_time,
                "last_error": self._last_error,
                "action_count": self._action_count,
            }

    def _set_status(self, status: AgentStatus, action: str | None = None):
        with self._lock:
            self._status = status
            if action:
                self._last_action = action
                self._last_action_time = datetime.now(timezone.utc).isoformat()
                self._action_count += 1

    # ── Logging shortcuts ──────────────────────────────────────────

    def log(self, action: str, details: dict | None = None,
            level: str = "INFO", duration_ms: int | None = None):
        self.logger.log(action, details, level, duration_ms)

    def log_decision(self, action: str, details: dict | None = None):
        """Log a trading decision (special level for frontend highlighting)."""
        self.logger.log(action, details, level="DECISION")

    # ── Bus shortcuts ──────────────────────────────────────────────

    def publish(self, msg_type: str, payload: dict, to_agent: str | None = None):
        self.bus.publish(self.name, msg_type, payload, to_agent)

    def consume(self, msg_types: list[str], limit: int = 50) -> list[dict]:
        return self.bus.consume(self.name, msg_types, limit)

    # ── Execution wrapper ──────────────────────────────────────────

    def execute(self, action_name: str, func, *args, **kwargs):
        """Execute a function with status tracking, timing, and error handling.

        Usage:
            result = self.execute("Collecting news", self._collect_news)
        """
        self._set_status(AgentStatus.WORKING, action_name)
        start = time.monotonic()
        try:
            result = func(*args, **kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            self.log(action_name, duration_ms=duration_ms)
            self._set_status(AgentStatus.IDLE, action_name)
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self.log(action_name, {"error": str(exc)}, level="ERROR",
                     duration_ms=duration_ms)
            with self._lock:
                self._status = AgentStatus.ERROR
                self._last_error = str(exc)
            raise

    # ── Override these ─────────────────────────────────────────────

    def run(self, **kwargs) -> dict:
        """Main agent execution. Override in subclasses."""
        raise NotImplementedError

    def get_metrics(self) -> dict:
        """Agent-specific metrics for the frontend. Override in subclasses."""
        return {}
