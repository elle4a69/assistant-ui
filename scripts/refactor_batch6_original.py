"""Refactor Batch 6: FastAPI Domain APIRouters Extraction.

Extracts domain services and APIRouters out of backend/main.py while maintaining
100% route contract parity and zero-loss backwards compatibility.
"""

from pathlib import Path
import re
import textwrap

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
MAIN_PATH = BACKEND_DIR / "main.py"

with open(MAIN_PATH, "r", encoding="utf-8") as f:
    MAIN_LINES = f.readlines()


def get_slice(start: int, end: int) -> str:
    """Get 1-indexed line slice inclusive."""
    return "".join(MAIN_LINES[start - 1 : end])


def get_route_slice(start: int, end: int) -> str:
    """Get 1-indexed line slice and replace @app. with @router."""
    block = get_slice(start, end)
    return block.replace("@app.", "@router.")


# ==============================================================================
# 1. SERVICES EXTRACTION
# ==============================================================================

# backend/services/knowledge_service.py
KNOWLEDGE_SERVICE_CODE = f'''"""Knowledge base and RAG retrieval service."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from backend.core.state import KNOWLEDGE_CHUNKS
    from backend.core.clients import openai_client
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from core.state import KNOWLEDGE_CHUNKS
    from core.clients import openai_client

{get_slice(1449, 1524)}

{get_slice(1528, 1563)}

{get_slice(1662, 1716)}

{get_slice(2846, 2862)}

__all__ = [
    "load_knowledge_base",
    "retrieve_knowledge_chunks",
    "search_knowledge",
    "resolve_knowledge_template",
    "match_qa_rule",
]
'''

# backend/services/settings_service.py
SETTINGS_SERVICE_CODE = f'''"""Settings, catalogue, business variables, and configuration service."""

from __future__ import annotations

import csv
import io
import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
    )
    from backend.core.constants import LINE_SERVICE_FILENAMES
    from backend.core.state import _quick_replies_lock
    from backend.core.utils import _safe_csv_cell
    from backend.models.domain import Thread, Message
    from backend.core.clients import load_line_profiles
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
    )
    from core.constants import LINE_SERVICE_FILENAMES
    from core.state import _quick_replies_lock
    from core.utils import _safe_csv_cell
    from models.domain import Thread, Message
    from core.clients import load_line_profiles

# Line services catalogue
{get_slice(1569, 1647)}

# Business variables
{get_slice(1719, 1860)}

# First contact autoresponder
{get_slice(2865, 2929)}

# Message UI and quick replies
{get_slice(4759, 4821)}

# CSV Export
{get_slice(5918, 5946)}

__all__ = [
    "_line_services_path",
    "_service_line_key",
    "_read_service_catalogue",
    "_ensure_line_service_catalogues",
    "load_line_services",
    "load_all_line_services",
    "get_live_services_context",
    "load_business_variables",
    "get_business_variable_values",
    "get_line_business_variable_values",
    "effective_line_user_prompt",
    "get_live_business_variables_context",
    "build_business_context",
    "account_allows_conversational_ai",
    "normalize_first_contact_autoresponder",
    "load_first_contact_autoresponders",
    "load_first_contact_autoresponder",
    "save_first_contact_autoresponders",
    "load_message_ui_settings",
    "default_quick_replies",
    "load_quick_replies",
    "save_quick_replies",
    "render_message_export_csv",
]
'''

# backend/services/learning_service.py
LEARNING_SERVICE_CODE = f'''"""Learned information and manual learning curation service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

try:
    from backend.core.config import DATA_DIR
    from backend.core.state import LEARNED_INFORMATION_LOCK
    from backend.core.clients import openai_client
    from backend.models.domain import Thread, Message
    from backend.curator.classifier import (
        classify_knowledge_candidate,
        prepare_learning_candidate,
        classify_knowledge_entries,
        classify_all_learned_information,
        _knowledge_meaning_signature,
        KNOWLEDGE_CATEGORIES,
        KNOWLEDGE_SCOPES,
    )
    from backend.curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from backend.services.knowledge_service import load_knowledge_base
except ImportError:
    from core.config import DATA_DIR
    from core.state import LEARNED_INFORMATION_LOCK
    from core.clients import openai_client
    from models.domain import Thread, Message
    from curator.classifier import (
        classify_knowledge_candidate,
        prepare_learning_candidate,
        classify_knowledge_entries,
        classify_all_learned_information,
        _knowledge_meaning_signature,
        KNOWLEDGE_CATEGORIES,
        KNOWLEDGE_SCOPES,
    )
    from curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from services.knowledge_service import load_knowledge_base

LEARNED_INFORMATION_FILE = os.path.join(DATA_DIR, "learned_rules.json")

{get_slice(1875, 1883)}

{get_slice(1948, 2087)}

{get_slice(2272, 2356)}

{get_slice(2488, 2643)}

{get_slice(2755, 2805)}

__all__ = [
    "LEARNED_INFORMATION_FILE",
    "_parse_json_object",
    "save_learned_information",
    "_upsert_learned_information_entry",
    "list_learned_information",
    "replace_learned_information_entry",
    "move_all_learned_information_to_review",
    "approve_learned_information_entry",
    "approve_pending_learned_information",
    "approve_selected_learned_information",
    "delete_learned_information_entry",
    "generate_manual_learning",
    "save_manual_learning",
    "redraft_learned_information_entry",
    "redraft_all_pending_learned_information",
    "save_edited_draft_learning",
]
'''

# backend/services/phone_service.py
PHONE_SERVICE_CODE = f'''"""Phone thread management, information request, and catch-up service."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

try:
    from backend.core.config import DEFAULT_CATCH_UP_LOOKBACK_DAYS
    from backend.core.constants import (
        INTERNAL_INSTRUCTION_REPLY_PATTERNS,
        UNSAFE_HOLDING_REPLY_PATTERNS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from backend.core.clients import openai_client, canonical_phone_number
    from backend.core.utils import format_dt, normalized_reply_fingerprint
    from backend.models.domain import Thread, Message, ThreadEvent, BlockedContact
    from backend.services.sms_service import find_thread_by_phone, run_sms_reply_logic
    from backend.services.learning_service import save_learned_information, _parse_json_object
except ImportError:
    from core.config import DEFAULT_CATCH_UP_LOOKBACK_DAYS
    from core.constants import (
        INTERNAL_INSTRUCTION_REPLY_PATTERNS,
        UNSAFE_HOLDING_REPLY_PATTERNS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from core.clients import openai_client, canonical_phone_number
    from core.utils import format_dt, normalized_reply_fingerprint
    from models.domain import Thread, Message, ThreadEvent, BlockedContact
    from services.sms_service import find_thread_by_phone, run_sms_reply_logic
    from services.learning_service import save_learned_information, _parse_json_object

{get_slice(1886, 1945)}

{get_slice(2808, 2826)}

{get_slice(3536, 3625)}

{get_slice(4469, 4480)}

__all__ = [
    "generate_information_request_content",
    "find_pending_information_request",
    "has_active_explicit_takeover",
    "list_catch_up_candidates",
    "find_oldest_catch_up_candidate",
    "_normalise_manual_reply_text",
    "_manual_reply_response",
]
'''

# backend/services/bootcamp_service.py
BOOTCAMP_SERVICE_CODE = f'''"""Bootcamp training reply generation service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import BOOTCAMP_STORE, BOOTCAMP_HANDOFF_RE, BOOTCAMP_REFUSAL_RE
    from backend.core.clients import openai_client
    from backend.services.learning_service import _parse_json_object, save_learned_information
except ImportError:
    from core.config import BOOTCAMP_STORE, BOOTCAMP_HANDOFF_RE, BOOTCAMP_REFUSAL_RE
    from core.clients import openai_client
    from services.learning_service import _parse_json_object, save_learned_information

{get_slice(6844, 7049)}

__all__ = [
    "generate_bootcamp_tori_reply",
    "generate_bootcamp_information_resolution",
    "generate_bootcamp_persona_reply",
]
'''

with open(BACKEND_DIR / "services" / "knowledge_service.py", "w", encoding="utf-8") as f:
    f.write(KNOWLEDGE_SERVICE_CODE)
print("Wrote backend/services/knowledge_service.py")

with open(BACKEND_DIR / "services" / "settings_service.py", "w", encoding="utf-8") as f:
    f.write(SETTINGS_SERVICE_CODE)
print("Wrote backend/services/settings_service.py")

with open(BACKEND_DIR / "services" / "learning_service.py", "w", encoding="utf-8") as f:
    f.write(LEARNING_SERVICE_CODE)
print("Wrote backend/services/learning_service.py")

with open(BACKEND_DIR / "services" / "phone_service.py", "w", encoding="utf-8") as f:
    f.write(PHONE_SERVICE_CODE)
print("Wrote backend/services/phone_service.py")

with open(BACKEND_DIR / "services" / "bootcamp_service.py", "w", encoding="utf-8") as f:
    f.write(BOOTCAMP_SERVICE_CODE)
print("Wrote backend/services/bootcamp_service.py")

# Update backend/services/__init__.py
SERVICES_INIT_CODE = '''"""Domain services package for assistant-ui backend.

Re-exports all symbols from domain service modules.
"""

from __future__ import annotations

from .auth_service import *
from .arrival_service import *
from .booking_service import *
from .sms_service import *
from .operations_service import *
from .knowledge_service import *
from .settings_service import *
from .learning_service import *
from .phone_service import *
from .bootcamp_service import *
from . import (
    auth_service,
    arrival_service,
    booking_service,
    sms_service,
    operations_service,
    knowledge_service,
    settings_service,
    learning_service,
    phone_service,
    bootcamp_service,
)

__all__ = [
    *auth_service.__all__,
    *arrival_service.__all__,
    *booking_service.__all__,
    *sms_service.__all__,
    *operations_service.__all__,
    *knowledge_service.__all__,
    *settings_service.__all__,
    *learning_service.__all__,
    *phone_service.__all__,
    *bootcamp_service.__all__,
]
'''

with open(BACKEND_DIR / "services" / "__init__.py", "w", encoding="utf-8") as f:
    f.write(SERVICES_INIT_CODE)
print("Updated backend/services/__init__.py")


# ==============================================================================
# 2. ROUTES EXTRACTION
# ==============================================================================

ROUTES_DIR = BACKEND_DIR / "routes"
ROUTES_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------------------
# 2.1 backend/routes/auth.py
# ------------------------------------------------------------------------------
AUTH_ROUTE_CODE = f'''"""Authentication and health routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

try:
    from backend.core.config import AUTH_PASSWORD, AUTH_COOKIE_NAME
    from backend.schemas.domain import AdminLoginInput
    from backend.services.auth_service import (
        _set_admin_session_cookie,
        _valid_admin_credentials,
        _valid_admin_session,
    )
except ImportError:
    from core.config import AUTH_PASSWORD, AUTH_COOKIE_NAME
    from schemas.domain import AdminLoginInput
    from services.auth_service import (
        _set_admin_session_cookie,
        _valid_admin_credentials,
        _valid_admin_session,
    )

router = APIRouter()

{get_route_slice(3023, 3026)}

{get_route_slice(3034, 3038)}

{get_route_slice(3041, 3046)}

{get_route_slice(3049, 3052)}

__all__ = [
    "router",
    "health_check",
    "admin_auth_status",
    "admin_auth_login",
    "admin_auth_logout",
]
'''
with open(ROUTES_DIR / "auth.py", "w", encoding="utf-8") as f:
    f.write(AUTH_ROUTE_CODE)
print("Wrote backend/routes/auth.py")


# ------------------------------------------------------------------------------
# 2.2 backend/routes/booking.py
# ------------------------------------------------------------------------------
BOOKING_ROUTE_CODE = f'''"""Booking, calendar, and availability routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import os
from typing import Any, Dict, List, Optional
import uuid
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

try:
    from backend.core.config import BOOKING_REMINDER_CONFIG_PATH, PROMPTS_DIR
    from backend.core.constants import DAY_NAMES
    from backend.core.database import get_db
    from backend.core.clients import calendar_service, mobilemessage_service
    from backend.core.utils import format_dt
    from backend.models.domain import CalendarEvent, Thread, Message, ThreadEvent
    from backend.schemas.domain import UpdateBookingInput, BookingReminderInput, ManualBookingInput
    from backend.services.booking_service import (
        BOOKING_PROVIDERS,
        load_working_hours,
        _booking_reminder_parts,
        parse_business_datetime,
        booking_availability_error,
        load_booking_reminder_config,
    )
    from backend.knowledge.style_retrieval import render_template_variables
    from backend.services.settings_service import load_line_services, get_business_variable_values
    from backend.services.arrival_service import _arrival_booking, _arrival_public_link, _issue_arrival_invite
    from backend.services.sms_service import find_thread_by_phone
except ImportError:
    from core.config import BOOKING_REMINDER_CONFIG_PATH, PROMPTS_DIR
    from core.constants import DAY_NAMES
    from core.database import get_db
    from core.clients import calendar_service, mobilemessage_service
    from core.utils import format_dt
    from models.domain import CalendarEvent, Thread, Message, ThreadEvent
    from schemas.domain import UpdateBookingInput, BookingReminderInput, ManualBookingInput
    from services.booking_service import (
        BOOKING_PROVIDERS,
        load_working_hours,
        _booking_reminder_parts,
        parse_business_datetime,
        booking_availability_error,
        load_booking_reminder_config,
    )
    from knowledge.style_retrieval import render_template_variables
    from services.settings_service import load_line_services, get_business_variable_values
    from services.arrival_service import _arrival_booking, _arrival_public_link, _issue_arrival_invite
    from services.sms_service import find_thread_by_phone

router = APIRouter()

{get_route_slice(3212, 3350)}

{get_route_slice(3358, 3444)}

{get_route_slice(3447, 3457)}

{get_route_slice(3466, 3511)}

{get_route_slice(6350, 6352)}

{get_route_slice(6354, 6359)}

{get_route_slice(6370, 6536)}

__all__ = [
    "router",
    "get_bookings",
    "update_booking_endpoint",
    "delete_booking_endpoint",
    "get_free_slots_endpoint",
    "get_booking_reminder_settings",
    "save_booking_reminder_settings",
    "create_manual_booking",
]
'''
with open(ROUTES_DIR / "booking.py", "w", encoding="utf-8") as f:
    f.write(BOOKING_ROUTE_CODE)
print("Wrote backend/routes/booking.py")


# ------------------------------------------------------------------------------
# 2.3 backend/routes/arrival.py
# ------------------------------------------------------------------------------
ARRIVAL_ROUTE_CODE = f'''"""Customer arrival and session routes."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service
    from backend.models.domain import ArrivalSession, ArrivalChatMessage, CalendarEvent, Thread, Message, ThreadEvent
    from backend.schemas.domain import (
        ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    )
    from backend.services.arrival_service import (
        _issue_arrival_invite,
        _arrival_public_link,
        _arrival_payload,
        _arrival_messages,
        _arrival_client_channel_message,
        _arrival_booking,
        _client_ip,
        _require_arrival_client,
        is_clear_customer_arrival,
        record_customer_arrival_event,
    )
    from backend.services.sms_service import find_thread_by_phone
except ImportError:
    from core.database import get_db
    from core.clients import mobilemessage_service
    from models.domain import ArrivalSession, ArrivalChatMessage, CalendarEvent, Thread, Message, ThreadEvent
    from schemas.domain import (
        ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    )
    from services.arrival_service import (
        _issue_arrival_invite,
        _arrival_public_link,
        _arrival_payload,
        _arrival_messages,
        _arrival_client_channel_message,
        _arrival_booking,
        _client_ip,
        _require_arrival_client,
        is_clear_customer_arrival,
        record_customer_arrival_event,
    )
    from services.sms_service import find_thread_by_phone

router = APIRouter()

{get_route_slice(3732, 3798)}

{get_route_slice(3801, 3875)}

{get_route_slice(3878, 3897)}

{get_route_slice(3900, 3903)}

{get_route_slice(3906, 3917)}

{get_route_slice(3920, 3932)}

{get_route_slice(3935, 3940)}

{get_route_slice(3943, 3956)}

{get_route_slice(3959, 3970)}

{get_route_slice(4367, 4439)}

__all__ = [
    "router",
    "create_arrival_invite",
    "activate_arrival",
    "get_arrival_invite_status",
    "get_client_arrival_session",
    "send_client_arrival_message",
    "list_arrival_sessions",
    "get_admin_arrival_session",
    "send_admin_arrival_message",
    "close_arrival_session",
    "acknowledge_thread_arrival",
]
'''
with open(ROUTES_DIR / "arrival.py", "w", encoding="utf-8") as f:
    f.write(ARRIVAL_ROUTE_CODE)
print("Wrote backend/routes/arrival.py")


# ------------------------------------------------------------------------------
# 2.4 backend/routes/phone.py
# ------------------------------------------------------------------------------
PHONE_ROUTE_CODE = f'''"""Phone threads, messaging, takeover, and review routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        DATA_DIR, AUTO_REPLY_GLOBAL_ENABLED, MANUAL_REPLY_DEDUPE_WINDOW,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, canonical_phone_number
    from backend.core.state import get_thread_lock, OUTBOUND_SMS_SEND_LOCK
    from backend.core.utils import format_dt, normalized_reply_fingerprint
    from backend.models.domain import (
        Thread, Message, ThreadEvent, BlockedContact, CalendarEvent, ArrivalSession
    )
    from backend.schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, NoteInput, EscalateInput, ResolveInput,
        InformationRequestResponseInput
    )
    from backend.services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
        generate_information_request_content,
    )
    from backend.services.learning_service import save_learned_information
    from backend.services.sms_service import (
        run_sms_reply_logic,
        find_thread_by_phone,
    )
except ImportError:
    from core.config import (
        DATA_DIR, AUTO_REPLY_GLOBAL_ENABLED, MANUAL_REPLY_DEDUPE_WINDOW,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from core.database import get_db
    from core.clients import mobilemessage_service, canonical_phone_number
    from core.state import get_thread_lock, OUTBOUND_SMS_SEND_LOCK
    from core.utils import format_dt, normalized_reply_fingerprint
    from models.domain import (
        Thread, Message, ThreadEvent, BlockedContact, CalendarEvent, ArrivalSession
    )
    from schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, NoteInput, EscalateInput, ResolveInput,
        InformationRequestResponseInput
    )
    from services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
        generate_information_request_content,
    )
    from services.learning_service import save_learned_information
    from services.sms_service import (
        run_sms_reply_logic,
        find_thread_by_phone,
    )

logger = logging.getLogger(__name__)

router = APIRouter()

{get_route_slice(3126, 3146)}

{get_route_slice(3149, 3157)}

{get_route_slice(3160, 3178)}

{get_route_slice(4048, 4188)}

{get_route_slice(4191, 4247)}

{get_route_slice(4250, 4338)}

{get_route_slice(4341, 4364)}

{get_route_slice(4442, 4464)}

{get_route_slice(4483, 4564)}

{get_route_slice(4567, 4675)}

{get_route_slice(4678, 4701)}

{get_route_slice(4704, 4724)}

{get_route_slice(4727, 4747)}

__all__ = [
    "router",
    "toggle_autoresponder",
    "set_thread_pinned",
    "set_thread_blocked",
    "get_threads",
    "catch_up_missed_messages",
    "get_thread_detail",
    "clear_thread_review_flags",
    "takeover_thread",
    "reply_thread",
    "respond_to_information_request",
    "add_thread_note",
    "escalate_thread",
    "resolve_thread",
]
'''
with open(ROUTES_DIR / "phone.py", "w", encoding="utf-8") as f:
    f.write(PHONE_ROUTE_CODE)
print("Wrote backend/routes/phone.py")


# ------------------------------------------------------------------------------
# 2.5 backend/routes/sms.py
# ------------------------------------------------------------------------------
SMS_ROUTE_CODE = f'''"""SMS webhook, simulator, and confirmation settings routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

try:
    from backend.core.config import DATA_DIR, PROMPTS_DIR
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, canonical_phone_number
    from backend.models.domain import Thread, Message, ThreadEvent, InboundWebhookReceipt
    from backend.schemas.domain import WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput
    from backend.services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
        should_process_sms_synchronously,
        inbound_webhook_identity,
    )
except ImportError:
    from core.config import DATA_DIR, PROMPTS_DIR
    from core.database import get_db
    from core.clients import mobilemessage_service, canonical_phone_number
    from models.domain import Thread, Message, ThreadEvent, InboundWebhookReceipt
    from schemas.domain import WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput
    from services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
        should_process_sms_synchronously,
        inbound_webhook_identity,
    )

SMS_CONFIRMATION_CONFIG_PATH = os.path.join(DATA_DIR, "sms_confirmation.json")

router = APIRouter()

{get_route_slice(3991, 4000)}

{get_route_slice(4003, 4045)}

{get_route_slice(6324, 6334)}

{get_route_slice(6337, 6346)}

__all__ = [
    "router",
    "webhook_sms",
    "simulate_inbound_sms",
    "get_sms_confirmation",
    "save_sms_confirmation",
]
'''
with open(ROUTES_DIR / "sms.py", "w", encoding="utf-8") as f:
    f.write(SMS_ROUTE_CODE)
print("Wrote backend/routes/sms.py")


# ------------------------------------------------------------------------------
# 2.6 backend/routes/curator.py
# ------------------------------------------------------------------------------
CURATOR_ROUTE_CODE = f'''"""Knowledge curation, learned rules, and knowledge files management routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, PERSIST_DIR
    from backend.core.database import get_db
    from backend.schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningCandidateInput, SmsLearningImportInput,
        FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from backend.services.learning_service import (
        generate_manual_learning,
        save_manual_learning,
        list_learned_information,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
    )
    from backend.services.knowledge_service import load_knowledge_base
    from backend.curator.classifier import classify_all_learned_information
    from backend.curator.service import (
        get_knowledge_curator_state,
        run_knowledge_curator,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
    )
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, PERSIST_DIR
    from core.database import get_db
    from schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningCandidateInput, SmsLearningImportInput,
        FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from services.learning_service import (
        generate_manual_learning,
        save_manual_learning,
        list_learned_information,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
    )
    from services.knowledge_service import load_knowledge_base
    from curator.classifier import classify_all_learned_information
    from curator.service import (
        get_knowledge_curator_state,
        run_knowledge_curator,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
    )

router = APIRouter()

{get_route_slice(5898, 5906)}

{get_route_slice(5965, 5967)}

{get_route_slice(5970, 5982)}

{get_route_slice(5985, 5993)}

{get_route_slice(5996, 5998)}

{get_route_slice(6001, 6006)}

{get_route_slice(6009, 6011)}

{get_route_slice(6014, 6017)}

{get_route_slice(6020, 6028)}

{get_route_slice(6031, 6033)}

{get_route_slice(6036, 6038)}

{get_route_slice(6041, 6047)}

{get_route_slice(6050, 6054)}

{get_route_slice(6057, 6059)}

{get_route_slice(6062, 6064)}

{get_route_slice(6067, 6074)}

{get_route_slice(6077, 6088)}

{get_route_slice(6091, 6099)}

{get_route_slice(6102, 6114)}

{get_route_slice(6117, 6129)}

{get_route_slice(6132, 6141)}

{get_route_slice(6149, 6161)}

{get_route_slice(6164, 6176)}

{get_route_slice(6179, 6191)}

{get_route_slice(6194, 6232)}

{get_route_slice(6235, 6284)}

__all__ = [
    "router",
    "create_manual_learning",
    "get_learned_information",
    "update_learned_information",
    "approve_learned_information",
    "approve_pending_learned_information_endpoint",
    "approve_selected_learned_information_endpoint",
    "sms_pair_learning_preview",
    "sms_pair_learning_import",
    "redraft_learned_information",
    "redraft_pending_learned_information",
    "move_all_learnings_to_review",
    "remove_learned_information",
    "classify_learned_information",
    "list_knowledge_curator_state",
    "run_knowledge_curator_endpoint",
    "accept_knowledge_curator_proposal_endpoint",
    "resolve_knowledge_curator_proposal_endpoint",
    "transition_knowledge_curator_proposal_endpoint",
    "get_knowledge_files",
    "upload_knowledge_file",
    "upload_credentials_file",
    "get_knowledge_file_content",
    "save_knowledge_file_content",
    "delete_knowledge_file",
    "search_knowledge_file_lines",
    "purge_knowledge_file_lines",
]
'''
with open(ROUTES_DIR / "curator.py", "w", encoding="utf-8") as f:
    f.write(CURATOR_ROUTE_CODE)
print("Wrote backend/routes/curator.py")


# ------------------------------------------------------------------------------
# 2.7 backend/routes/settings.py
# ------------------------------------------------------------------------------
SETTINGS_ROUTE_CODE = f'''"""Settings, business variables, line profiles, and configurations routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BUSINESS_VARIABLES_PATH, WORKING_HOURS_PATH, MESSAGE_UI_SETTINGS_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, QUICK_REPLY_ACCOUNT_KEYS,
        FIRST_CONTACT_AUTORESPONDER_DEFAULT,
    )
    from backend.core.state import TRAINING_MODE_ENABLED
    from backend.core.database import get_db
    from backend.core.clients import (
        load_line_profiles, save_line_profiles,
        mobilemessage_service, canonical_phone_number
    )
    from backend.core.utils import format_dt
    from backend.models.domain import BlockedContact, Thread, Message
    from backend.schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderInput,
        WorkingHoursInput, MobileMessageConfigInput,
    )
    from backend.services.booking_service import load_working_hours
    from backend.services.settings_service import (
        load_business_variables,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )
except ImportError:
    from core.config import (
        BUSINESS_VARIABLES_PATH, WORKING_HOURS_PATH, MESSAGE_UI_SETTINGS_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, QUICK_REPLY_ACCOUNT_KEYS,
        FIRST_CONTACT_AUTORESPONDER_DEFAULT,
    )
    from core.state import TRAINING_MODE_ENABLED
    from core.database import get_db
    from core.clients import (
        load_line_profiles, save_line_profiles,
        mobilemessage_service, canonical_phone_number
    )
    from core.utils import format_dt
    from models.domain import BlockedContact, Thread, Message
    from schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderInput,
        WorkingHoursInput, MobileMessageConfigInput,
    )
    from services.booking_service import load_working_hours
    from services.settings_service import (
        load_business_variables,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )

router = APIRouter()

{get_route_slice(3181, 3191)}

{get_route_slice(3194, 3209)}

{get_route_slice(5381, 5383)}

{get_route_slice(5386, 5418)}

{get_route_slice(5421, 5423)}

{get_route_slice(5426, 5446)}

{get_route_slice(5449, 5454)}

{get_route_slice(5457, 5482)}

{get_route_slice(5485, 5529)}

{get_route_slice(5532, 5606)}

{get_route_slice(5637, 5644)}

{get_route_slice(5646, 5665)}

{get_route_slice(5949, 5962)}

{get_route_slice(6547, 6549)}

{get_route_slice(6552, 6561)}

{get_route_slice(6571, 6592)}

{get_route_slice(6594, 6603)}

__all__ = [
    "router",
    "list_blocked_contacts",
    "unblock_contact",
    "get_business_variables",
    "save_business_variables",
    "get_line_profiles",
    "update_line_profiles",
    "get_quick_replies",
    "update_quick_reply",
    "get_settings",
    "update_settings",
    "get_first_contact_autoresponder",
    "save_first_contact_autoresponder",
    "export_messages_csv",
    "get_working_hours",
    "save_working_hours",
    "get_mobilemessage_settings",
    "save_mobilemessage_settings",
]
'''
with open(ROUTES_DIR / "settings.py", "w", encoding="utf-8") as f:
    f.write(SETTINGS_ROUTE_CODE)
print("Wrote backend/routes/settings.py")


# ------------------------------------------------------------------------------
# 2.8 backend/routes/operations.py
# ------------------------------------------------------------------------------
OPERATIONS_ROUTE_CODE = f'''"""Operations AI, agent console, and realtime session routes."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        AGENT_CONSOLE_PROTOCOL_VERSION,
        OPERATIONS_WORKER_PROTOCOL_VERSION,
    )
    from backend.core.database import get_db
    from backend.core.clients import openai_client
    from backend.models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage, OperationsMemory,
        Thread, Message, ThreadEvent
    )
    from backend.schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput,
        OperationsVoiceToolInput
    )
    from backend.services.operations_service import (
        _serialize_agent_run,
        _serialize_agent_event,
        _create_agent_run,
        _request_agent_cancel,
        _stream_agent_run,
        _agent_websocket_origin_allowed,
        _operations_claim_worker_task,
        generate_operations_ai_response,
        record_operations_chat_message,
        ensure_operations_owner_working_style,
        create_operations_realtime_session,
        build_operations_ai_snapshot,
        build_operations_ai_memory_context,
        _build_agent_conversation_context,
        persist_operations_realtime_turn,
        execute_operations_voice_tool,
    )
except ImportError:
    from core.config import (
        AGENT_CONSOLE_PROTOCOL_VERSION,
        OPERATIONS_WORKER_PROTOCOL_VERSION,
    )
    from core.database import get_db
    from core.clients import openai_client
    from models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage, OperationsMemory,
        Thread, Message, ThreadEvent
    )
    from schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput,
        OperationsVoiceToolInput
    )
    from services.operations_service import (
        _serialize_agent_run,
        _serialize_agent_event,
        _create_agent_run,
        _request_agent_cancel,
        _stream_agent_run,
        _agent_websocket_origin_allowed,
        _operations_claim_worker_task,
        generate_operations_ai_response,
        record_operations_chat_message,
        ensure_operations_owner_working_style,
        create_operations_realtime_session,
        build_operations_ai_snapshot,
        build_operations_ai_memory_context,
        _build_agent_conversation_context,
        persist_operations_realtime_turn,
        execute_operations_voice_tool,
    )

logger = logging.getLogger(__name__)

router = APIRouter()

{get_route_slice(5066, 5077)}

{get_route_slice(5080, 5096)}

{get_route_slice(5099, 5195)}

{get_route_slice(5198, 5209)}

{get_route_slice(5212, 5221)}

{get_route_slice(5224, 5325)}

{get_route_slice(5328, 5349)}

{get_route_slice(5352, 5357)}

{get_route_slice(5360, 5365)}

__all__ = [
    "router",
    "list_agent_console_runs",
    "list_agent_console_events",
    "operations_agent_websocket",
    "claim_operations_worker_task",
    "get_operations_chat_messages",
    "send_operations_chat_message",
    "start_operations_realtime_session",
    "save_operations_realtime_turn",
    "run_operations_realtime_tool",
]
'''
with open(ROUTES_DIR / "operations.py", "w", encoding="utf-8") as f:
    f.write(OPERATIONS_ROUTE_CODE)
print("Wrote backend/routes/operations.py")


# ------------------------------------------------------------------------------
# 2.9 backend/routes/bootcamp.py
# ------------------------------------------------------------------------------
BOOTCAMP_ROUTE_CODE = f'''"""AI Bootcamp training, personas, and simulations routes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

try:
    from backend.core.config import BOOTCAMP_STORE
    from backend.schemas.domain import (
        BootcampRunInput, BootcampControlInput, BootcampProfileApplyInput,
        BootcampInformationRequestInput,
    )
    from backend.services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )
    from backend.services.learning_service import save_learned_information
except ImportError:
    from core.config import BOOTCAMP_STORE
    from schemas.domain import (
        BootcampRunInput, BootcampControlInput, BootcampProfileApplyInput,
        BootcampInformationRequestInput,
    )
    from services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )
    from services.learning_service import save_learned_information

router = APIRouter()

{get_route_slice(7070, 7072)}

{get_route_slice(7075, 7082)}

{get_route_slice(7085, 7091)}

{get_route_slice(7094, 7101)}

{get_route_slice(7104, 7115)}

{get_route_slice(7118, 7120)}

{get_route_slice(7123, 7128)}

{get_route_slice(7131, 7178)}

{get_route_slice(7181, 7191)}

{get_route_slice(7194, 7200)}

__all__ = [
    "router",
    "get_bootcamp_personas",
    "get_bootcamp_profile",
    "apply_bootcamp_profile",
    "undo_bootcamp_profile",
    "start_bootcamp_run",
    "get_latest_bootcamp_run",
    "get_bootcamp_run",
    "respond_to_bootcamp_information_request",
    "control_bootcamp_run",
    "reset_bootcamp_runs",
]
'''
with open(ROUTES_DIR / "bootcamp.py", "w", encoding="utf-8") as f:
    f.write(BOOTCAMP_ROUTE_CODE)
print("Wrote backend/routes/bootcamp.py")


# ------------------------------------------------------------------------------
# 2.10 backend/routes/misc.py
# ------------------------------------------------------------------------------
serve_spa_raw = get_slice(7210, 7224)
serve_spa_unindented = textwrap.dedent(serve_spa_raw).replace("@app.", "@router.")

MISC_ROUTE_CODE = f'''"""Miscellaneous routes: push notifications, shortlinks, RAG status, QA rules, draft messages, and SPA serving."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

try:
    from backend.core.config import BASE_DIR, DATA_DIR
    from backend.core.state import TRAINING_MODE_ENABLED
    from backend.core.constants import FIRST_CONTACT_ACCOUNT_KEYS, DAY_NAMES
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, canonical_phone_number, openai_client
    from backend.core.state import _vapid_key_lock, get_thread_lock
    from backend.models.domain import (
        PushSubscription, CalendarEvent, ArrivalSession, Thread, Message, ThreadEvent
    )
    from backend.schemas.domain import (
        PushSubscriptionInput,
        DraftUpdateInput, LocantoMessagePayload,
    )
    from backend.services.arrival_service import (
        _push_configured,
        _ensure_persistent_vapid_keypair,
        _vapid_public_key,
    )
    from backend.services.learning_service import save_edited_draft_learning
    from backend.services.settings_service import (
        load_all_line_services,
        _line_services_path,
        load_line_services,
        get_business_variable_values,
        build_business_context,
    )
    from backend.services.knowledge_service import match_qa_rule
    from backend.services.sms_service import run_sms_reply_logic, find_thread_by_phone
except ImportError:
    from core.config import BASE_DIR, DATA_DIR
    from core.state import TRAINING_MODE_ENABLED
    from core.constants import FIRST_CONTACT_ACCOUNT_KEYS, DAY_NAMES
    from core.database import get_db
    from core.clients import mobilemessage_service, canonical_phone_number, openai_client
    from core.state import _vapid_key_lock, get_thread_lock
    from models.domain import (
        PushSubscription, CalendarEvent, ArrivalSession, Thread, Message, ThreadEvent
    )
    from schemas.domain import (
        PushSubscriptionInput,
        DraftUpdateInput, LocantoMessagePayload,
    )
    from services.arrival_service import (
        _push_configured,
        _ensure_persistent_vapid_keypair,
        _vapid_public_key,
    )
    from services.learning_service import save_edited_draft_learning
    from services.settings_service import (
        load_all_line_services,
        _line_services_path,
        load_line_services,
        get_business_variable_values,
        build_business_context,
    )
    from services.knowledge_service import match_qa_rule
    from services.sms_service import run_sms_reply_logic, find_thread_by_phone

logger = logging.getLogger(__name__)

frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))

router = APIRouter()

{get_route_slice(3681, 3688)}

{get_route_slice(3691, 3707)}

{get_route_slice(3710, 3714)}

{get_route_slice(3717, 3729)}

{get_route_slice(5368, 5378)}

{get_route_slice(5614, 5623)}

{get_route_slice(5626, 5635)}

{get_route_slice(5668, 5754)}

{get_route_slice(5757, 5784)}

{get_route_slice(5787, 5818)}

{get_route_slice(5821, 5851)}

{get_route_slice(5854, 5895)}

{get_route_slice(6300, 6302)}

{get_route_slice(6305, 6321)}

{get_route_slice(6612, 6837)}

{serve_spa_unindented}

__all__ = [
    "router",
    "get_push_config",
    "save_push_subscription",
    "delete_push_subscription",
    "follow_arrival_short_link",
    "get_rag_admin_status",
    "get_qa_rules",
    "save_qa_rules",
    "approve_draft_message",
    "update_draft_message",
    "discard_draft_message",
    "clear_pending_draft_messages",
    "clear_review_only_threads",
    "get_services",
    "save_services",
    "handle_locanto_message",
    "serve_spa",
]
'''
with open(ROUTES_DIR / "misc.py", "w", encoding="utf-8") as f:
    f.write(MISC_ROUTE_CODE)
print("Wrote backend/routes/misc.py")


# ------------------------------------------------------------------------------
# 2.11 backend/routes/__init__.py
# ------------------------------------------------------------------------------
ROUTES_INIT_CODE = '''"""FastAPI Domain APIRouters package for assistant-ui backend.

Re-exports all modular routers and route handlers.
"""

from __future__ import annotations

from .auth import *
from .auth import router as auth_router
from .booking import *
from .booking import router as booking_router
from .arrival import *
from .arrival import router as arrival_router
from .phone import *
from .phone import router as phone_router
from .sms import *
from .sms import router as sms_router
from .curator import *
from .curator import router as curator_router
from .settings import *
from .settings import router as settings_router
from .operations import *
from .operations import router as operations_router
from .bootcamp import *
from .bootcamp import router as bootcamp_router
from .misc import *
from .misc import router as misc_router

from . import (
    auth,
    booking,
    arrival,
    phone,
    sms,
    curator,
    settings,
    operations,
    bootcamp,
    misc,
)

__all__ = [
    "auth_router",
    "booking_router",
    "arrival_router",
    "phone_router",
    "sms_router",
    "curator_router",
    "settings_router",
    "operations_router",
    "bootcamp_router",
    "misc_router",
    *auth.__all__,
    *booking.__all__,
    *arrival.__all__,
    *phone.__all__,
    *sms.__all__,
    *curator.__all__,
    *settings.__all__,
    *operations.__all__,
    *bootcamp.__all__,
    *misc.__all__,
]
'''
with open(ROUTES_DIR / "__init__.py", "w", encoding="utf-8") as f:
    f.write(ROUTES_INIT_CODE)
print("Wrote backend/routes/__init__.py")
