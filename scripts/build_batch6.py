import ast
import os
import py_compile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PATH = REPO_ROOT / "backend" / "main.py"
SERVICES_DIR = REPO_ROOT / "backend" / "services"
ROUTES_DIR = REPO_ROOT / "backend" / "routes"

with open(MAIN_PATH, "r", encoding="utf-8") as f:
    main_code = f.read()

tree = ast.parse(main_code)
lines = main_code.splitlines(keepends=True)

funcs = {}
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        funcs[node.name] = node
    elif isinstance(node, ast.If):
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                funcs[sub.name] = sub

def get_src(name, replace_app=False):
    node = funcs[name]
    decorators = getattr(node, "decorator_list", [])
    start = min([d.lineno for d in decorators] + [node.lineno])
    end = node.end_lineno
    src = "".join(lines[start - 1 : end])
    if replace_app:
        src = src.replace("@app.", "@router.")
    return src

print(f"AST indexed {len(funcs)} functions/classes.")
SERVICES_MAP = {
    "knowledge_service.py": (
        """\"\"\"Knowledge base and RAG retrieval service.\"\"\"

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from backend.core.state import KNOWLEDGE_CHUNKS
    from backend.core.clients import openai_client, get_line_profile, resolve_provider_context
    from backend.core.constants import APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES, TEMPLATE_VARIABLE_PATTERN
    from backend.services.settings_service import get_line_business_variable_values, build_business_context
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from core.state import KNOWLEDGE_CHUNKS
    from core.clients import openai_client, get_line_profile, resolve_provider_context
    from core.constants import APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES, TEMPLATE_VARIABLE_PATTERN
    from services.settings_service import get_line_business_variable_values, build_business_context

logger = logging.getLogger(__name__)
""",
        [
            "load_knowledge_base",
            "retrieve_knowledge_chunks",
            "search_knowledge",
            "validate_knowledge_template_variables",
            "resolve_knowledge_template",
            "match_qa_rule",
        ],
        [],
    ),
    "settings_service.py": (
        """\"\"\"Settings, catalogue, business variables, and configuration service.\"\"\"

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
        BUSINESS_VARIABLE_DEFAULTS,
    )
    from backend.core.constants import LINE_SERVICE_FILENAMES
    from backend.core.state import _quick_replies_lock
    from backend.core.utils import _safe_csv_cell
    from backend.models.domain import Thread, Message, BlockedContact
    from backend.core.clients import load_line_profiles, canonical_phone_number
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        MESSAGE_UI_SETTINGS_PATH, QUICK_REPLIES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, FIRST_CONTACT_ACCOUNT_KEYS,
        QUICK_REPLY_ACCOUNT_KEYS, QUICK_REPLY_DEFAULT_LABELS,
        MESSAGE_EXPORT_COLUMNS, FIRST_CONTACT_AUTORESPONDER_DEFAULT,
        BUSINESS_VARIABLE_DEFAULTS,
    )
    from core.constants import LINE_SERVICE_FILENAMES
    from core.state import _quick_replies_lock
    from core.utils import _safe_csv_cell
    from models.domain import Thread, Message, BlockedContact
    from core.clients import load_line_profiles, canonical_phone_number

logger = logging.getLogger(__name__)
""",
        [
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
        ],
        [],
    ),
    "learning_service.py": (
        """\"\"\"Learned information and manual learning curation service.\"\"\"

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
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
        _quarantined_knowledge_classification,
    )
    from backend.curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from backend.curator.authority import (
        resolve_knowledge_authority,
        normalize_knowledge_record,
        _canonical_knowledge_key,
        _knowledge_reason,
    )
    from backend.curator.service import _approve_curator_supersession_entry
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
        _quarantined_knowledge_classification,
    )
    from curator.sanitizer import (
        has_unsafe_literal_learning_detail,
        shared_knowledge_is_generic,
        sanitise_reusable_knowledge_template,
        _learning_other_provider_detail,
    )
    from curator.authority import (
        resolve_knowledge_authority,
        normalize_knowledge_record,
        _canonical_knowledge_key,
        _knowledge_reason,
    )
    from curator.service import _approve_curator_supersession_entry
    from services.knowledge_service import load_knowledge_base

logger = logging.getLogger(__name__)

LEARNED_INFORMATION_FILE = os.path.join(DATA_DIR, "learned_rules.json")
""",
        [
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
        ],
        ["LEARNED_INFORMATION_FILE"],
    ),
    "phone_service.py": (
        """\"\"\"Phone thread management, information request, and catch-up service.\"\"\"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BASE_DIR, DATA_DIR, FIRST_CONTACT_AUTORESPONDER_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        INTERNAL_INSTRUCTION_REPLY_PATTERNS, UNSAFE_HOLDING_REPLY_PATTERNS,
        SERVICE_AND_BOOKING_CONVERSATION_POLICY, RELEVANCE_AND_THREAD_FLOW_POLICY,
        SMS_TYPOGRAPHY_POLICY,
    )
    from backend.core.state import (
        SMS_REPLY_THREAD_LOCKS, SMS_REPLY_GLOBAL_LOCK, get_thread_lock,
    )
    from backend.core.clients import (
        openai_client, mobilemessage_service, canonical_phone_number,
    )
    from backend.core.utils import normalized_reply_fingerprint, format_dt
    from backend.models.domain import Thread, Message, ThreadEvent
    from backend.services.settings_service import (
        account_allows_conversational_ai, load_first_contact_autoresponder,
        load_first_contact_autoresponders, load_business_variables,
        get_business_variable_values,
    )
    from backend.services.booking_service import (
        current_business_time, build_read_only_calendar_context,
    )
    from backend.services.sms_service import (
        human_replied_after, is_latest_customer_turn, build_model_input,
        build_model_instructions, is_contact_blocked,
    )
    from backend.knowledge.style_retrieval import get_style_examples
    from backend.knowledge import render_template_variables
except ImportError:
    from core.config import (
        BASE_DIR, DATA_DIR, FIRST_CONTACT_AUTORESPONDER_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS, CONVERSATIONAL_AI_ACCOUNT_KEYS,
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        INTERNAL_INSTRUCTION_REPLY_PATTERNS, UNSAFE_HOLDING_REPLY_PATTERNS,
        SERVICE_AND_BOOKING_CONVERSATION_POLICY, RELEVANCE_AND_THREAD_FLOW_POLICY,
        SMS_TYPOGRAPHY_POLICY,
    )
    from core.state import (
        SMS_REPLY_THREAD_LOCKS, SMS_REPLY_GLOBAL_LOCK, get_thread_lock,
    )
    from core.clients import (
        openai_client, mobilemessage_service, canonical_phone_number,
    )
    from core.utils import normalized_reply_fingerprint, format_dt
    from models.domain import Thread, Message, ThreadEvent
    from services.settings_service import (
        account_allows_conversational_ai, load_first_contact_autoresponder,
        load_first_contact_autoresponders, load_business_variables,
        get_business_variable_values,
    )
    from services.booking_service import (
        current_business_time, build_read_only_calendar_context,
    )
    from services.sms_service import (
        human_replied_after, is_latest_customer_turn, build_model_input,
        build_model_instructions, is_contact_blocked,
    )
    from knowledge.style_retrieval import get_style_examples
    from knowledge import render_template_variables

logger = logging.getLogger(__name__)
""",
        [
            "generate_information_request_content",
            "find_pending_information_request",
            "has_active_explicit_takeover",
            "list_catch_up_candidates",
            "find_oldest_catch_up_candidate",
            "_normalise_manual_reply_text",
            "_manual_reply_response",
        ],
        [],
    ),
    "bootcamp_service.py": (
        """\"\"\"AI Bootcamp training, personas, and simulations service.\"\"\"

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE
    from backend.core.clients import openai_client
except ImportError:
    from core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE
    from core.clients import openai_client

logger = logging.getLogger(__name__)
""",
        [
            "generate_bootcamp_tori_reply",
            "generate_bootcamp_information_resolution",
            "generate_bootcamp_persona_reply",
        ],
        [],
    ),
}

for fname, (hdr, fnames, extra_exports) in SERVICES_MAP.items():
    blocks = [get_src(fn) for fn in fnames]
    all_exports = extra_exports + fnames
    content = f"{hdr}\n" + "\n\n".join(blocks) + "\n\n__all__ = [\n" + "\n".join(f'    "{e}",' for e in all_exports) + "\n]\n"
    with open(SERVICES_DIR / fname, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Wrote services/{fname}")
ROUTERS_MAP_PART1 = {
    "auth.py": (
        """\"\"\"Authentication and health routes.\"\"\"

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
""",
        [
            "health_check",
            "admin_auth_status",
            "admin_auth_login",
            "admin_auth_logout",
        ],
    ),
    "booking.py": (
        """\"\"\"Booking, calendar, and availability routes.\"\"\"

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
        get_service_for_booking,
        load_booking_services,
    )
    from backend.services.settings_service import get_business_variable_values, load_business_variables
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
        get_service_for_booking,
        load_booking_services,
    )
    from services.settings_service import get_business_variable_values, load_business_variables
    from services.sms_service import find_thread_by_phone

router = APIRouter()
""",
        [
            "get_bookings",
            "update_booking_endpoint",
            "delete_booking_endpoint",
            "get_free_slots_endpoint",
            "get_booking_reminder_settings",
            "save_booking_reminder_settings",
            "create_manual_booking",
        ],
    ),
    "arrival.py": (
        """\"\"\"Customer arrival and session routes.\"\"\"

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service, get_line_profile
    from backend.core.utils import format_dt
    from backend.models.domain import ArrivalSession, ArrivalChatMessage, Thread, ThreadEvent, Message
    from backend.schemas.domain import ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    from backend.services.arrival_service import (
        _issue_arrival_invite,
        _activate_arrival_session,
        _client_arrival_session_dict,
        _client_arrival_message_dict,
        _admin_arrival_session_dict,
        _admin_arrival_message_dict,
        send_arrival_push_notifications,
        send_arrival_clear_notifications,
        resolve_arrival_line_key,
        ARRIVAL_PIN_TTL_HOURS,
    )
    from backend.services.auth_service import _valid_admin_session
    from backend.core.config import AUTH_COOKIE_NAME
except ImportError:
    from core.database import get_db
    from core.clients import mobilemessage_service, get_line_profile
    from core.utils import format_dt
    from models.domain import ArrivalSession, ArrivalChatMessage, Thread, ThreadEvent, Message
    from schemas.domain import ArrivalInviteInput, ArrivalActivateInput, ArrivalMessageInput
    from services.arrival_service import (
        _issue_arrival_invite,
        _activate_arrival_session,
        _client_arrival_session_dict,
        _client_arrival_message_dict,
        _admin_arrival_session_dict,
        _admin_arrival_message_dict,
        send_arrival_push_notifications,
        send_arrival_clear_notifications,
        resolve_arrival_line_key,
        ARRIVAL_PIN_TTL_HOURS,
    )
    from services.auth_service import _valid_admin_session
    from core.config import AUTH_COOKIE_NAME

router = APIRouter()
""",
        [
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
        ],
    ),
    "phone.py": (
        """\"\"\"Phone threads, messaging, takeover, and review routes.\"\"\"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import canonical_phone_number, mobilemessage_service, openai_client
    from backend.core.utils import format_dt, normalized_reply_fingerprint
    from backend.core.state import get_thread_lock, SMS_REPLY_GLOBAL_LOCK
    from backend.core.config import (
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        CONVERSATIONAL_AI_ACCOUNT_KEYS, FIRST_CONTACT_ACCOUNT_KEYS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from backend.models.domain import Thread, Message, ThreadEvent, Note, BlockedContact
    from backend.schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, InformationRequestResponseInput,
        NoteInput, EscalateInput, ResolveInput,
    )
    from backend.services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
    )
    from backend.services.sms_service import (
        find_thread_by_phone,
        send_sms_message_with_delivery_tracking,
        run_sms_reply_logic,
        is_contact_blocked,
    )
    from backend.services.arrival_service import resolve_arrival_line_key
    from backend.services.booking_service import current_business_time
except ImportError:
    from core.database import get_db
    from core.clients import canonical_phone_number, mobilemessage_service, openai_client
    from core.utils import format_dt, normalized_reply_fingerprint
    from core.state import get_thread_lock, SMS_REPLY_GLOBAL_LOCK
    from core.config import (
        DEFAULT_CATCH_UP_LOOKBACK_DAYS, MANUAL_REPLY_DEDUPE_WINDOW,
        CONVERSATIONAL_AI_ACCOUNT_KEYS, FIRST_CONTACT_ACCOUNT_KEYS,
        TAKEOVER_RELEASE_EVENT_TYPES,
    )
    from models.domain import Thread, Message, ThreadEvent, Note, BlockedContact
    from schemas.domain import (
        AutoresponderInput, ThreadPinnedInput, ThreadBlockedInput,
        TakeoverInput, ReplyInput, InformationRequestResponseInput,
        NoteInput, EscalateInput, ResolveInput,
    )
    from services.phone_service import (
        has_active_explicit_takeover,
        list_catch_up_candidates,
        find_oldest_catch_up_candidate,
        _normalise_manual_reply_text,
        _manual_reply_response,
        find_pending_information_request,
    )
    from services.sms_service import (
        find_thread_by_phone,
        send_sms_message_with_delivery_tracking,
        run_sms_reply_logic,
        is_contact_blocked,
    )
    from services.arrival_service import resolve_arrival_line_key
    from services.booking_service import current_business_time

router = APIRouter()
""",
        [
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
        ],
    ),
    "sms.py": (
        """\"\"\"SMS webhook, simulator, and confirmation settings routes.\"\"\"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service
    from backend.models.domain import Thread, Message
    from backend.schemas.domain import (
        WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput,
    )
    from backend.services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
    )
except ImportError:
    from core.database import get_db
    from core.clients import mobilemessage_service
    from models.domain import Thread, Message
    from schemas.domain import (
        WebhookSMSInput, AdminSmsSimulationInput, SmsConfirmationInput,
    )
    from services.sms_service import (
        process_inbound_sms,
        run_sms_reply_logic,
    )

router = APIRouter()
""",
        [
            "webhook_sms",
            "simulate_inbound_sms",
            "get_sms_confirmation",
            "save_sms_confirmation",
        ],
    ),
    "curator.py": (
        """\"\"\"Knowledge curation, learned rules, and knowledge files management routes.\"\"\"

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
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR
    from backend.core.database import get_db
    from backend.models.domain import Thread, Message
    from backend.schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningImportInput, FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from backend.curator import (
        inspect_knowledge_integrity,
        run_knowledge_curator,
        get_knowledge_curator_state,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
        KnowledgeCuratorService,
    )
    from backend.services.learning_service import (
        list_learned_information,
        save_manual_learning,
        generate_manual_learning,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
        save_edited_draft_learning,
        LEARNED_INFORMATION_FILE,
    )
    from backend.services.knowledge_service import load_knowledge_base
    from backend.curator.classifier import classify_all_learned_information
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR
    from core.database import get_db
    from models.domain import Thread, Message
    from schemas.domain import (
        ManualLearningInput, LearnedInformationUpdateInput,
        LearnedInformationBulkApproveInput, SmsLearningPreviewInput,
        SmsLearningImportInput, FileSaveInput, FileSearchInput, FilePurgeInput,
    )
    from curator import (
        inspect_knowledge_integrity,
        run_knowledge_curator,
        get_knowledge_curator_state,
        accept_knowledge_curator_proposal,
        resolve_knowledge_curator_proposal,
        transition_knowledge_curator_proposal,
        KnowledgeCuratorService,
    )
    from services.learning_service import (
        list_learned_information,
        save_manual_learning,
        generate_manual_learning,
        replace_learned_information_entry,
        approve_learned_information_entry,
        approve_pending_learned_information,
        approve_selected_learned_information,
        redraft_learned_information_entry,
        redraft_all_pending_learned_information,
        move_all_learned_information_to_review,
        delete_learned_information_entry,
        save_edited_draft_learning,
        LEARNED_INFORMATION_FILE,
    )
    from services.knowledge_service import load_knowledge_base
    from curator.classifier import classify_all_learned_information

router = APIRouter()
""",
        [
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
        ],
    ),
}

for fname, (hdr, fnames) in ROUTERS_MAP_PART1.items():
    blocks = [get_src(fn, replace_app=True) for fn in fnames]
    content = f"{hdr}\n" + "\n\n".join(blocks) + "\n\n__all__ = [\n    \"router\",\n" + "\n".join(f'    "{e}",' for e in fnames) + "\n]\n"
    with open(ROUTES_DIR / fname, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Wrote routes/{fname}")
ROUTERS_MAP_PART2 = {
    "settings.py": (
        """\"\"\"Settings, business variables, line profiles, and configurations routes.\"\"\"

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

try:
    from backend.core.config import (
        BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS,
    )
    from backend.core.database import get_db
    from backend.core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service,
    )
    from backend.models.domain import BlockedContact, Thread, Message
    from backend.schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput,
        WorkingHoursInput, MobileMessageConfigInput,
    )
    from backend.services.settings_service import (
        load_business_variables,
        get_business_variable_values,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )
    from backend.services.booking_service import (
        load_working_hours,
        save_working_hours,
    )
except ImportError:
    from core.config import (
        BUSINESS_VARIABLES_PATH, LINE_PROFILES_PATH,
        FIRST_CONTACT_AUTORESPONDER_PATH, WORKING_HOURS_PATH,
        FIRST_CONTACT_ACCOUNT_KEYS,
    )
    from core.database import get_db
    from core.clients import (
        load_line_profiles, save_line_profiles, get_line_profile,
        canonical_phone_number, mobilemessage_service,
    )
    from models.domain import BlockedContact, Thread, Message
    from schemas.domain import (
        BusinessVariablesInput, LineProfilesInput, QuickReplyInput,
        SettingsUpdateInput, FirstContactAutoresponderAccountsInput,
        WorkingHoursInput, MobileMessageConfigInput,
    )
    from services.settings_service import (
        load_business_variables,
        get_business_variable_values,
        load_quick_replies,
        save_quick_replies,
        load_message_ui_settings,
        load_first_contact_autoresponders,
        save_first_contact_autoresponders,
        render_message_export_csv,
    )
    from services.booking_service import (
        load_working_hours,
        save_working_hours,
    )

router = APIRouter()
""",
        [
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
        ],
    ),
    "operations.py": (
        """\"\"\"Operations AI, agent console, and realtime session routes.\"\"\"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

try:
    from backend.core.database import get_db, SessionLocal
    from backend.core.utils import format_dt
    from backend.core.state import _agent_run_tasks, _agent_start_lock
    from backend.models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage,
    )
    from backend.schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput, OperationsRealtimeToolInput,
    )
    from backend.services.operations_service import (
        _run_agent_console,
        _stream_agent_run,
        _agent_websocket_authenticated,
        _agent_websocket_origin_allowed,
        execute_runtime_change,
        _prune_agent_console_history,
    )
except ImportError:
    from core.database import get_db, SessionLocal
    from core.utils import format_dt
    from core.state import _agent_run_tasks, _agent_start_lock
    from models.domain import (
        OperationsAgentRun, OperationsAgentEvent, OperationsChatMessage,
    )
    from schemas.domain import (
        OperationsChatInput, OperationsRealtimeTurnInput, OperationsRealtimeToolInput,
    )
    from services.operations_service import (
        _run_agent_console,
        _stream_agent_run,
        _agent_websocket_authenticated,
        _agent_websocket_origin_allowed,
        execute_runtime_change,
        _prune_agent_console_history,
    )

router = APIRouter()
""",
        [
            "list_agent_console_runs",
            "list_agent_console_events",
            "operations_agent_websocket",
            "claim_operations_worker_task",
            "get_operations_chat_messages",
            "send_operations_chat_message",
            "start_operations_realtime_session",
            "save_operations_realtime_turn",
            "run_operations_realtime_tool",
        ],
    ),
    "bootcamp.py": (
        """\"\"\"AI Bootcamp training, personas, and simulations routes.\"\"\"

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

try:
    from backend.core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE
    from backend.schemas.domain import (
        BootcampProfileInput, BootcampProfileApplyInput,
        BootcampRunInput, BootcampControlInput,
        BootcampInformationRequestInput,
    )
    from backend.services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )
except ImportError:
    from core.config import BOOTCAMP_STORE, BOOTCAMP_OPENINGS_FILE
    from schemas.domain import (
        BootcampProfileInput, BootcampProfileApplyInput,
        BootcampRunInput, BootcampControlInput,
        BootcampInformationRequestInput,
    )
    from services.bootcamp_service import (
        generate_bootcamp_tori_reply,
        generate_bootcamp_information_resolution,
        generate_bootcamp_persona_reply,
    )

router = APIRouter()
""",
        [
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
        ],
    ),
}

for fname, (hdr, fnames) in ROUTERS_MAP_PART2.items():
    blocks = [get_src(fn, replace_app=True) for fn in fnames]
    content = f"{hdr}\n" + "\n\n".join(blocks) + "\n\n__all__ = [\n    \"router\",\n" + "\n".join(f'    "{e}",' for e in fnames) + "\n]\n"
    with open(ROUTES_DIR / fname, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Wrote routes/{fname}")

# MISC ROUTER
misc_route_funcs = [
    "get_push_config",
    "save_push_subscription",
    "delete_push_subscription",
    "follow_arrival_short_link",
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
]
misc_blocks = [get_src(fn, replace_app=True) for fn in misc_route_funcs]

rag_status_code = """@router.get("/api/admin/rag/status")
@router.get("/api/rag/status")
def get_rag_admin_status():
    \"\"\"Return RAG index admin status, dataset hash, intent breakdown, and validation status.\"\"\"
    if not is_style_examples_enabled() or get_example_index() is None:
        raise HTTPException(
            status_code=503,
            detail="Style example retrieval is unavailable (feature flag disabled or dataset not indexed)",
        )
    return get_example_index().get_status_metadata()"""

spa_code = """frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))

@router.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
        return None

    # Public landing page served at root "/"
    if not full_path or full_path == "":
        landing_path = os.path.join(frontend_dist, "landing.html")
        if os.path.exists(landing_path):
            return FileResponse(landing_path)

    file_path = os.path.join(frontend_dist, full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(frontend_dist, "index.html"))"""

all_misc_funcs = ["get_rag_admin_status"] + misc_route_funcs + ["serve_spa"]

misc_code = f"""\"\"\"Miscellaneous routes: push notifications, shortlinks, RAG status, QA rules, draft messages, and SPA serving.\"\"\"

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

try:
    from backend.core.config import BASE_DIR, DATA_DIR, STYLE_PROFILE_STORE
    from backend.core.database import get_db
    from backend.core.clients import mobilemessage_service
    from backend.core.state import _vapid_key_lock
    from backend.core.utils import format_dt
    from backend.models.domain import (
        PushSubscription, ArrivalSession, Thread, Message, ThreadEvent,
    )
    from backend.schemas.domain import (
        PushSubscriptionInput, DraftUpdateInput, QARuleItem,
        ServicesListInput, LocantoMessagePayload,
    )
    from backend.knowledge import (
        is_style_examples_enabled,
        get_example_index,
        DATASET_FILE,
    )
    from backend.services.arrival_service import (
        _push_configured,
        WEB_PUSH_AVAILABLE,
        _vapid_public_key,
        _ensure_arrival_token_index,
    )
    from backend.services.sms_service import (
        find_thread_by_phone,
        send_sms_message_with_delivery_tracking,
        run_sms_reply_logic,
    )
    from backend.services.settings_service import (
        load_line_services,
    )
except ImportError:
    from core.config import BASE_DIR, DATA_DIR, STYLE_PROFILE_STORE
    from core.database import get_db
    from core.clients import mobilemessage_service
    from core.state import _vapid_key_lock
    from core.utils import format_dt
    from models.domain import (
        PushSubscription, ArrivalSession, Thread, Message, ThreadEvent,
    )
    from schemas.domain import (
        PushSubscriptionInput, DraftUpdateInput, QARuleItem,
        ServicesListInput, LocantoMessagePayload,
    )
    from knowledge import (
        is_style_examples_enabled,
        get_example_index,
        DATASET_FILE,
    )
    from services.arrival_service import (
        _push_configured,
        WEB_PUSH_AVAILABLE,
        _vapid_public_key,
        _ensure_arrival_token_index,
    )
    from services.sms_service import (
        find_thread_by_phone,
        send_sms_message_with_delivery_tracking,
        run_sms_reply_logic,
    )
    from services.settings_service import (
        load_line_services,
    )

router = APIRouter()

{rag_status_code}

""" + "\n\n".join(misc_blocks) + f"""

{spa_code}

__all__ = [
    "router",
""" + "\n".join(f'    "{fn}",' for fn in all_misc_funcs) + """
]
"""
with open(ROUTES_DIR / "misc.py", "w", encoding="utf-8") as f:
    f.write(misc_code)
print("Wrote routes/misc.py")
# ==============================================================================
# 7. MAIN.PY COMPOSITION ROOT
# ==============================================================================
main_content = """\"\"\"Assistant UI Backend Application Composition Root.

Wires together FastAPI application, middlewares, domain APIRouters, lifespan/startup
workers, and re-exports symbols for zero-loss backwards compatibility.
\"\"\"

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Core leaf modules
from backend.core.config import *
from backend.core.constants import *
from backend.core.database import *
from backend.core.clients import *
from backend.core.state import *
from backend.core.utils import *

# Domain models & schemas
from backend.models import *
from backend.schemas import *

# Domain services
from backend.services import *

# Curator
from backend.curator import *

# Knowledge & RAG
from backend.knowledge import (
    render_template_variables,
    set_style_examples_enabled,
    is_style_examples_enabled,
    set_example_index,
    get_example_index,
    STYLE_EXAMPLES_ENABLED,
    example_index,
    DATASET_FILE,
)

# Domain APIRouters
try:
    from anon_content import router as anon_content_router
except ImportError:
    from backend.anon_content import router as anon_content_router

from backend.routes.auth import router as auth_router
from backend.routes.booking import router as booking_router
from backend.routes.arrival import router as arrival_router
from backend.routes.phone import router as phone_router
from backend.routes.sms import router as sms_router
from backend.routes.curator import router as curator_router
from backend.routes.settings import router as settings_router
from backend.routes.operations import router as operations_router
from backend.routes.bootcamp import router as bootcamp_router
from backend.routes.misc import router as misc_router
from backend.routes import *

# Initialize FastAPI application
app = FastAPI(title="Assistant UI Backend")

# Include anonymous content router
app.include_router(anon_content_router)

# Include domain APIRouters
app.include_router(auth_router)
app.include_router(booking_router)
app.include_router(arrival_router)
app.include_router(phone_router)
app.include_router(sms_router)
app.include_router(curator_router)
app.include_router(settings_router)
app.include_router(operations_router)
app.include_router(bootcamp_router)

# Mount static frontend assets and SPA fallback router
frontend_dist = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    assets_dir = os.path.join(frontend_dist, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
app.include_router(misc_router)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_api_response_caching(request: Request, call_next):
    \"\"\"Keep shared API state and stable live booking entry points fresh.\"\"\"
    response = await call_next(request)
    stable_live_paths = {"/", "/landing.html", "/booking", "/booking-inline.js"} | PORTAL_SPA_PATHS
    if request.url.path.startswith("/api/") or request.url.path in stable_live_paths:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.middleware("http")
async def require_basic_auth(request: Request, call_next):
    if is_public_request(request):
        return await call_next(request)

    if not AUTH_PASSWORD:
        return await call_next(request)

    if _valid_admin_session(request.cookies.get(AUTH_COOKIE_NAME, "")):
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    supplied_username = ""
    supplied_password = ""

    if scheme.lower() == "basic" and encoded:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            supplied_username, separator, supplied_password = decoded.partition(":")
            if not separator:
                supplied_username = ""
                supplied_password = ""
        except (ValueError, UnicodeDecodeError):
            pass

    if _valid_admin_credentials(supplied_username, supplied_password):
        response = await call_next(request)
        _set_admin_session_cookie(response, request)
        return response

    headers = {}
    if not request.url.path.startswith("/api/"):
        headers["WWW-Authenticate"] = 'Basic realm="Assistant UI", charset="UTF-8"'
    return Response(content="Authentication required.", status_code=401, headers=headers)


# Startup Event Workers
@app.on_event("startup")
async def start_arrival_alert_worker():
    asyncio.create_task(arrival_alert_worker())


def _interrupt_agent_run_if_orphaned(run_id: str) -> None:
    db = SessionLocal()
    try:
        run = db.query(OperationsAgentRun).filter(OperationsAgentRun.id == run_id).first()
        if run and run.status in AGENT_CONSOLE_ACTIVE_STATUSES and run.id not in _agent_run_tasks:
            _record_agent_chat_message(db, run, "user", run.objective)
            run.status = "interrupted"
            db.commit()
    finally:
        db.close()


def _interrupt_orphaned_agent_runs(db) -> None:
    runs = db.query(OperationsAgentRun).filter(OperationsAgentRun.status.in_(AGENT_CONSOLE_ACTIVE_STATUSES)).all()
    for run in runs:
        if run.id not in _agent_run_tasks:
            run.status = "interrupted"
    db.commit()


@app.on_event("startup")
def recover_interrupted_agent_console_runs() -> None:
    \"\"\"Never leave a volatile orchestration marked as live after a process restart.\"\"\"
    db = SessionLocal()
    try:
        _interrupt_orphaned_agent_runs(db)
        _prune_agent_console_history(db)
    finally:
        db.close()


def _prune_agent_console_history_once() -> None:
    db = SessionLocal()
    try:
        _prune_agent_console_history(db)
    finally:
        db.close()


async def _agent_console_retention_worker() -> None:
    while True:
        await asyncio.sleep(3600)
        await asyncio.to_thread(_prune_agent_console_history_once)


@app.on_event("startup")
async def start_agent_console_retention_worker() -> None:
    asyncio.create_task(_agent_console_retention_worker())


@app.on_event("startup")
async def start_booking_reminder_worker():
    asyncio.create_task(booking_reminder_worker())


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8025))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
"""

with open(MAIN_PATH, "w", encoding="utf-8") as f:
    f.write(main_content)
print("Wrote clean backend/main.py composition root!")
