"""Neutral configuration leaf module.

Contains base paths, volume persistence setup, environment-derived settings,
and re-exports all static constants from `backend.core.constants`.
This module MUST NOT import anything from `backend.main` or any router module.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import timedelta
from pathlib import Path

# Load environment variables defensively
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# Re-export all static constants from backend.core.constants
try:
    from backend.core.constants import *
except ImportError:
    from core.constants import *

# ===========================================================================
# Base Directories & Filesystem Paths
# ===========================================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
os.environ["SQLITE_TMPDIR"] = TMP_DIR
os.makedirs(TMP_DIR, exist_ok=True)

# Mount check: use /data if it is mounted as a Fly.io volume, or fallback to local BASE_DIR
PERSIST_DIR = "/data" if os.path.exists("/data") else BASE_DIR

DATA_DIR = os.path.join(PERSIST_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

KNOWLEDGE_DIR = os.path.join(PERSIST_DIR, "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

PROMPTS_DIR = os.path.join(PERSIST_DIR, "prompts")
os.makedirs(PROMPTS_DIR, exist_ok=True)

AGENT_RUNS_DIR = Path(PERSIST_DIR) / "agent-runs"

# Copy default templates and data files if migrating to mounted persistent volume /data
if PERSIST_DIR == "/data":
    src_db = os.path.join(BASE_DIR, "assistant.db")
    dest_db = os.path.join(PERSIST_DIR, "assistant.db")
    if os.path.exists(src_db) and (not os.path.exists(dest_db) or os.path.getsize(dest_db) < 100):
        try:
            shutil.copy2(src_db, dest_db)
            print("[Volume Migration] Copied seed assistant.db to /data/assistant.db")
        except Exception as e:
            print(f"[Volume Migration] Failed to copy assistant.db: {e}")

    src_bootcamp_db = os.path.join(BASE_DIR, "bootcamp.db")
    dest_bootcamp_db = os.path.join(PERSIST_DIR, "bootcamp.db")
    if os.path.exists(src_bootcamp_db) and (not os.path.exists(dest_bootcamp_db) or os.path.getsize(dest_bootcamp_db) < 100):
        try:
            shutil.copy2(src_bootcamp_db, dest_bootcamp_db)
            print("[Volume Migration] Copied seed bootcamp.db to /data/bootcamp.db")
        except Exception as e:
            print(f"[Volume Migration] Failed to copy bootcamp.db: {e}")

    src_prompts = os.path.join(BASE_DIR, "prompts")
    if os.path.exists(src_prompts):
        for item in os.listdir(src_prompts):
            src_file = os.path.join(src_prompts, item)
            dest_file = os.path.join(PROMPTS_DIR, item)
            if os.path.isfile(src_file) and not os.path.exists(dest_file):
                try:
                    shutil.copy2(src_file, dest_file)
                except Exception as e:
                    print(f"Failed to copy prompt default {item}: {e}")

    src_data = os.path.join(BASE_DIR, "data")
    if os.path.exists(src_data):
        for item in os.listdir(src_data):
            src_file = os.path.join(src_data, item)
            dest_file = os.path.join(DATA_DIR, item)
            if os.path.isfile(src_file) and not os.path.exists(dest_file):
                try:
                    shutil.copy2(src_file, dest_file)
                except Exception as e:
                    print(f"Failed to copy data default {item}: {e}")

# Stores
try:
    from backend.bootcamp import BootcampStore, StyleProfileStore
except ImportError:
    from bootcamp import BootcampStore, StyleProfileStore

STYLE_PROFILE_STORE = StyleProfileStore(DATA_DIR)
BOOTCAMP_STORE = BootcampStore(os.path.join(PERSIST_DIR, "bootcamp.db"))

# Specific configuration file paths
BOOTCAMP_OPENINGS_FILE = Path(
    os.getenv("BOOTCAMP_OPENINGS_FILE", os.path.join(BASE_DIR, "bootcamp_openings.jsonl"))
)
BUSINESS_VARIABLES_PATH = os.path.join(DATA_DIR, "business_variables.json")
LINE_PROFILES_PATH = os.path.join(DATA_DIR, "sms_line_profiles.json")
WORKING_HOURS_PATH = os.path.join(DATA_DIR, "working_hours.json")
MESSAGE_UI_SETTINGS_PATH = os.path.join(DATA_DIR, "message_ui_settings.json")
QUICK_REPLIES_PATH = os.path.join(DATA_DIR, "quick_replies.json")
FIRST_CONTACT_AUTORESPONDER_PATH = os.path.join(DATA_DIR, "first_contact_autoresponder.json")
BOOKING_REMINDER_CONFIG_PATH = os.path.join(DATA_DIR, "booking_reminder.json")
BOOKING_REMINDER_SENT_PATH = os.path.join(DATA_DIR, "booking_reminders_sent.json")
OPERATIONS_WORKER_WORKFLOW_PATH = ".github/workflows/operations-code.yml"

DB_FILE = os.path.join(PERSIST_DIR, "assistant.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_FILE}")

# ===========================================================================
# Authentication & Environment-Derived Configuration
# ===========================================================================

AUTH_USERNAME = os.getenv("APP_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("APP_PASSWORD", "")
AUTH_COOKIE_NAME = "assistant_ui_admin_session"
AUTH_SESSION_MAX_AGE = 60 * 60 * 24 * 365

# Global flag to enable/disable auto-replies
AUTO_REPLY_GLOBAL_ENABLED = True
auto_reply_path = os.path.join(DATA_DIR, "auto_reply_global.json")
if os.path.exists(auto_reply_path):
    try:
        with open(auto_reply_path, "r", encoding="utf-8") as f:
            AUTO_REPLY_GLOBAL_ENABLED = json.load(f).get("enabled", True)
    except Exception:
        pass

MANUAL_REPLY_DEDUPE_WINDOW = timedelta(minutes=5)
DEFAULT_CATCH_UP_LOOKBACK_DAYS = 3

AGENT_CONSOLE_HISTORY_LIMIT = 50
AGENT_CONSOLE_HISTORY_DAYS = 30
AGENT_CONSOLE_WORKSPACE_LIMIT_BYTES = 16 * 1024 * 1024
AGENT_CONSOLE_CONTEXT_MAX_CHARS = 18_000
AGENT_CONSOLE_MEMORY_MAX_CHARS = 6_000
AGENT_CONSOLE_CONTEXT_MESSAGE_LIMIT = 60
AGENT_CONSOLE_CONTEXT_LEGACY_RUN_LIMIT = 12
AGENT_CONSOLE_ACTION_TIMEOUT_SECONDS = 30
AGENT_CONSOLE_CODING_SUBMISSION_RESERVED_SECONDS = 7

BOOKING_REMINDER_LOCK = threading.Lock()

PORT = int(os.getenv("PORT", 8025))
port = PORT
