"""Neutral shared state and concurrency primitives leaf module.

Contains process-level locks, mutable caches, and in-memory synchronization primitives.
This module MUST NOT import anything from `backend.main`, `main`, or any route module.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Dict, List, Set

try:
    from backend.core.config import BOOKING_REMINDER_LOCK
except ImportError:
    try:
        from core.config import BOOKING_REMINDER_LOCK
    except ImportError:
        BOOKING_REMINDER_LOCK = threading.Lock()

# Knowledge & Learning locks
LEARNED_INFORMATION_LOCK: threading.Lock = threading.Lock()
KNOWLEDGE_CURATOR_LOCK: threading.Lock = threading.Lock()

# Outbound SMS transport lock
OUTBOUND_SMS_SEND_LOCK: threading.Lock = threading.Lock()

# Thread-level SMS reply serialization locks
SMS_REPLY_GLOBAL_LOCK: threading.Lock = threading.Lock()
SMS_REPLY_THREAD_LOCKS: Dict[str, threading.Lock] = defaultdict(threading.Lock)


def get_thread_lock(thread_id: str) -> threading.Lock:
    """Return or allocate an in-memory lock for serializing reply processing per thread."""
    with SMS_REPLY_GLOBAL_LOCK:
        return SMS_REPLY_THREAD_LOCKS[thread_id]


# Autonomous Operations Console locks and state
_agent_start_lock: threading.Lock = threading.Lock()
_agent_event_lock: threading.RLock = threading.RLock()
_agent_run_tasks: Dict[str, Any] = {}

# Operations code sandbox locks
_operations_code_task_lock: threading.Lock = threading.Lock()
_operations_code_deployment_lock: threading.Lock = threading.Lock()

# Message UI and web push locks
_quick_replies_lock: threading.Lock = threading.Lock()
_vapid_key_lock: threading.Lock = threading.Lock()

# Operations code security boundaries
OPERATIONS_CODE_BLOCKED_PARTS: Set[str] = {
    ".codex-secrets",
    ".git",
    ".ops-worktrees",
    ".venv",
    ".vscode",
    "__pycache__",
    "node_modules",
}

OPERATIONS_CODE_BLOCKED_NAMES: Set[str] = {
    ".env",
    "credentials.json",
    "service_account.json",
    "settings.json",
}

# In-memory knowledge base chunks cache
KNOWLEDGE_CHUNKS: List[Dict[str, Any]] = []

# Global training mode flag
TRAINING_MODE_ENABLED: bool = False

__all__ = [
    "LEARNED_INFORMATION_LOCK",
    "KNOWLEDGE_CURATOR_LOCK",
    "OUTBOUND_SMS_SEND_LOCK",
    "SMS_REPLY_GLOBAL_LOCK",
    "SMS_REPLY_THREAD_LOCKS",
    "get_thread_lock",
    "_agent_start_lock",
    "_agent_event_lock",
    "_agent_run_tasks",
    "_operations_code_task_lock",
    "_operations_code_deployment_lock",
    "_quick_replies_lock",
    "_vapid_key_lock",
    "BOOKING_REMINDER_LOCK",
    "OPERATIONS_CODE_BLOCKED_PARTS",
    "OPERATIONS_CODE_BLOCKED_NAMES",
    "KNOWLEDGE_CHUNKS",
    "TRAINING_MODE_ENABLED",
]

