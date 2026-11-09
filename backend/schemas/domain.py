"""Pydantic request and response schemas leaf module.

Contains validation and serialization models for API boundaries.
This module MUST NOT import anything from `backend.main`, `main`, or any route module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

try:
    from backend.core.config import FIRST_CONTACT_ACCOUNT_KEYS
except ImportError:
    try:
        from core.config import FIRST_CONTACT_ACCOUNT_KEYS
    except ImportError:
        FIRST_CONTACT_ACCOUNT_KEYS = ("primary",)

try:
    from backend.bootcamp import DEFAULT_STYLE_PROFILE
except ImportError:
    try:
        from bootcamp import DEFAULT_STYLE_PROFILE
    except ImportError:
        DEFAULT_STYLE_PROFILE = {
            "verbosity": 2,
            "warmth": 4,
            "directness": 3,
            "humor": 2,
            "emoji_usage": 1,
        }


# --- SMS Webhook & Transport Schemas ---

class WebhookSMSInput(BaseModel):
    from_phone: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    body: Optional[str] = None
    providerMessageId: Optional[str] = None
    originalMessageId: Optional[str] = None
    webhookType: Optional[str] = None
    receivedAt: Optional[datetime] = None
    isSimulation: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "from" not in data and "sender" in data:
                data["from"] = data["sender"]
            if "body" not in data and "message" in data:
                data["body"] = data["message"]
            if "receivedAt" not in data:
                if "received_at" in data:
                    data["receivedAt"] = data["received_at"]
                else:
                    data["receivedAt"] = datetime.utcnow().isoformat() + "Z"
            if "providerMessageId" not in data:
                # Mobile Message's inbound webhook does not document an inbound
                # message_id. original_message_id identifies the earlier outbound
                # SMS and therefore must never be used as the inbound identity.
                data["providerMessageId"] = data.get("message_id")
            if "originalMessageId" not in data:
                data["originalMessageId"] = data.get("original_message_id")
            if "webhookType" not in data:
                data["webhookType"] = data.get("type")
        return data

    class Config:
        populate_by_name = True


class AdminSmsSimulationInput(BaseModel):
    customer_phone: str
    body: str
    sms_account_key: Literal["primary"] = "primary"


class BulkSmsSendInput(BaseModel):
    """A single, owner-confirmed generic SMS delivered to several mobile numbers."""

    recipients: List[str] = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1600)
    smsAccountKey: Literal["primary"] = "primary"
    clientRequestId: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def clean_bulk_sms(self):
        self.recipients = [recipient.strip() for recipient in self.recipients if recipient.strip()]
        self.message = self.message.strip()
        self.clientRequestId = self.clientRequestId.strip()
        if not self.recipients:
            raise ValueError("Add at least one mobile number.")
        if not self.message:
            raise ValueError("Message is required.")
        if not self.clientRequestId:
            raise ValueError("A send request ID is required.")
        return self


# --- Thread Operational Schemas ---

class TakeoverInput(BaseModel):
    agentId: str


class ReplyInput(BaseModel):
    agentId: str
    text: str = Field(min_length=1, max_length=1600)
    clientRequestId: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def clean_reply(self):
        self.text = self.text.strip()
        self.clientRequestId = (self.clientRequestId or "").strip() or None
        if not self.text:
            raise ValueError("Reply text is required.")
        return self


class DraftUpdateInput(BaseModel):
    text: str = Field(min_length=1, max_length=1600)

    @model_validator(mode="after")
    def clean_draft(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Draft text is required.")
        return self


class MessageCorrectionInput(BaseModel):
    """Owner feedback on a particular AI response, kept out of live knowledge."""

    correction: str = Field(min_length=1, max_length=6000)
    suggested_reply: Optional[str] = Field(default=None, max_length=1600)

    @model_validator(mode="after")
    def clean_correction(self):
        self.correction = self.correction.strip()
        self.suggested_reply = (self.suggested_reply or "").strip() or None
        if not self.correction:
            raise ValueError("Tell us what should change.")
        return self


class NoteInput(BaseModel):
    agentId: str
    text: str


class EscalateInput(BaseModel):
    agentId: str
    reason: str


class ResolveInput(BaseModel):
    agentId: str
    resolution: str


class AutoresponderInput(BaseModel):
    enabled: bool


class ThreadPinnedInput(BaseModel):
    pinned: bool


class ThreadBlockedInput(BaseModel):
    blocked: bool


class FirstContactAutoresponderInput(BaseModel):
    enabled: bool = False
    cooldownDays: int = Field(default=30, ge=1, le=3650)
    delaySeconds: int = Field(default=0, ge=0, le=3600)
    message: str = Field(default="", max_length=1600)

    @model_validator(mode="after")
    def require_message_when_enabled(self):
        self.message = self.message.strip()
        if self.enabled and not self.message:
            raise ValueError("A reply message is required when the first-contact auto-responder is enabled.")
        return self


class InformationRequestResponseInput(BaseModel):
    agentId: str = Field(default="user", min_length=1, max_length=100)
    information: str = Field(min_length=1, max_length=6000)
    requestEventId: Optional[str] = None

    @model_validator(mode="after")
    def clean_information(self):
        self.agentId = self.agentId.strip() or "user"
        self.information = self.information.strip()
        if not self.information:
            raise ValueError("Information is required.")
        return self


class FirstContactAutoresponderAccountsInput(BaseModel):
    accounts: Dict[str, FirstContactAutoresponderInput]

    @model_validator(mode="after")
    def require_known_accounts(self):
        unknown = set(self.accounts) - set(FIRST_CONTACT_ACCOUNT_KEYS)
        if unknown:
            raise ValueError(f"Unknown SMS account: {sorted(unknown)[0]}")
        for key in FIRST_CONTACT_ACCOUNT_KEYS:
            if key not in self.accounts:
                raise ValueError(f"Missing SMS account: {key}")
        return self


# --- Knowledge & Learning Schemas ---

class ManualLearningInput(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    guidance: str = Field(min_length=1, max_length=6000)
    scope: Literal["shared"] = "shared"

    @model_validator(mode="after")
    def clean_learning(self):
        self.topic = self.topic.strip()
        self.guidance = self.guidance.strip()
        if not self.topic or not self.guidance:
            raise ValueError("Both a topic and guidance are required.")
        return self


class LearnedInformationUpdateInput(BaseModel):
    topic: str = Field(default="", max_length=500)
    text: str = Field(min_length=1, max_length=6000)
    scope: Literal["shared", "internal"]

    @model_validator(mode="after")
    def clean_entry(self):
        self.topic = self.topic.strip()
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Learning text is required.")
        return self


class LearnedInformationBulkApproveInput(BaseModel):
    entry_ids: List[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def clean_entry_ids(self):
        self.entry_ids = list(dict.fromkeys(entry_id.strip() for entry_id in self.entry_ids if entry_id.strip()))
        if not self.entry_ids:
            raise ValueError("Select at least one learned rule.")
        return self


class SmsLearningPreviewInput(BaseModel):
    limit: int = Field(default=50, ge=5, le=100)


class SmsLearningCandidateInput(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    account_key: Literal["primary"] = "primary"
    topic: str = Field(min_length=1, max_length=1200)
    applies_when: str = Field(min_length=1, max_length=1200)
    instruction: str = Field(min_length=1, max_length=1200)
    example_reply: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def clean_candidate(self):
        for field_name in ("id", "topic", "applies_when", "instruction", "example_reply"):
            setattr(self, field_name, str(getattr(self, field_name)).strip())
        return self


class SmsLearningImportInput(BaseModel):
    candidates: List[SmsLearningCandidateInput] = Field(min_length=1, max_length=100)


# --- Arrival Experience Schemas ---

class ArrivalInviteInput(BaseModel):
    summary: str = Field(min_length=1, max_length=300)
    customerPhone: Optional[str] = Field(default=None, max_length=50)
    smsAccountKey: Optional[Literal["primary"]] = "primary"
    threadId: Optional[str] = Field(default=None, max_length=100)
    startTime: datetime
    endTime: datetime


class ArrivalActivateInput(BaseModel):
    inviteToken: str = Field(min_length=16, max_length=200)


class ArrivalMessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def clean_text(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("Message is required.")
        return self


# --- Push Subscription Schemas ---

class PushSubscriptionKeysInput(BaseModel):
    p256dh: str = Field(min_length=20, max_length=500)
    auth: str = Field(min_length=8, max_length=200)


class PushSubscriptionInput(BaseModel):
    endpoint: str = Field(min_length=20, max_length=4000)
    expirationTime: Optional[float] = None
    keys: PushSubscriptionKeysInput

    @model_validator(mode="after")
    def require_https_endpoint(self):
        if not self.endpoint.startswith("https://"):
            raise ValueError("Push subscription endpoint must use HTTPS.")
        return self


# --- Authentication Schemas ---

class AdminLoginInput(BaseModel):
    username: str
    password: str


# --- Booking & Calendar Schemas ---

class UpdateBookingInput(BaseModel):
    summary: Optional[str] = None
    customerPhone: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    amount: Optional[int] = None


class ManualBookingInput(BaseModel):
    serviceId: str
    name: str
    phone: str
    startTime: str
    notes: Optional[str] = None
    providerKey: Literal["tori"] = "tori"


class BookingReminderInput(BaseModel):
    enabled: bool = True
    minutesBefore: int = Field(default=60, ge=5, le=10080)
    template: str = Field(min_length=1, max_length=4000)


class SmsConfirmationInput(BaseModel):
    template: str


# --- Business Variables & Line Profiles ---

class BusinessVariableInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=4000)
    description: Optional[str] = None
    required: Optional[bool] = False


class BusinessVariablesInput(BaseModel):
    variables: List[BusinessVariableInput] = Field(max_length=50)


class LineProfileInput(BaseModel):
    displayName: str = Field(default="", max_length=100)
    providerName: str = Field(default="", max_length=100)
    informationUrl: str = Field(default="", max_length=2000)
    autoReplyEnabled: bool = True
    timezone: Optional[str] = Field(default="Australia/Hobart", max_length=100)


class LineProfilesInput(BaseModel):
    primary: LineProfileInput


class LineAutoReplyInput(BaseModel):
    enabled: bool


class SettingsUpdateInput(BaseModel):
    openaiApiKey: Optional[str] = None
    systemPrompt: Optional[str] = None
    userPrompt: Optional[str] = None
    autoReplyGlobalEnabled: Optional[bool] = None
    trainingModeEnabled: Optional[bool] = None
    showMessageAvatars: Optional[bool] = None
    catchUpLookbackDays: Optional[int] = Field(default=None, ge=1, le=30)


class QuickReplyInput(BaseModel):
    label: str = Field(min_length=1, max_length=8)
    content: str = Field(default="", max_length=4000)


# --- Operations Console & Voice Schemas ---

class OperationsChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class OperationsVoiceToolInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    arguments: Dict[str, Any] = Field(default_factory=dict)

# Backward-compatible alias
OperationsRealtimeToolInput = OperationsVoiceToolInput


class OperationsRealtimeTurnInput(BaseModel):
    sessionId: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
    )
    userItemId: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    responseId: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    userTranscript: str = Field(min_length=1, max_length=8000)
    assistantTranscript: str = Field(min_length=1, max_length=8000)


# --- QA Rules, Files & Services Schemas ---

class QARuleItem(BaseModel):
    id: str
    trigger: str
    reply: str


class FileSaveInput(BaseModel):
    content: str


class FileSearchInput(BaseModel):
    query: str


class FilePurgeInput(BaseModel):
    query: Optional[str] = None
    indices: Optional[List[int]] = None


class ServiceItem(BaseModel):
    id: str
    name: str
    description: str
    price: int
    duration: int
    showDuration: Optional[bool] = True
    isActive: Optional[bool] = True
    addonIds: List[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def normalize_addon_ids(self):
        self.addonIds = list(dict.fromkeys(value.strip() for value in self.addonIds if value.strip()))
        return self


class ServicesListInput(BaseModel):
    services: List[ServiceItem]


class ServiceAddOnItem(BaseModel):
    """A shared optional extra that may be offered with a booked service."""

    id: str
    name: str
    description: str = ""
    price: int = Field(default=0, ge=0)
    duration: int = Field(default=0, ge=0)


class ServiceAddOnsInput(BaseModel):
    addons: List[ServiceAddOnItem]


ServiceAddonItem = ServiceAddOnItem
ServiceAddonsInput = ServiceAddOnsInput


class WorkingHourEntry(BaseModel):
    day: str
    enabled: bool
    open: str
    close: str


class WorkingHoursInput(BaseModel):
    hours: List[WorkingHourEntry]

class AvailabilitySlotDay(BaseModel):
    day: str
    enabled: bool = True
    open: Optional[str] = "09:00"
    close: Optional[str] = "17:00"
    slots: List[str] = Field(default_factory=list)

class AvailabilityConfigInput(BaseModel):
    maxDaysAhead: int = Field(default=14, ge=1, le=365)
    weeklyHours: List[AvailabilitySlotDay]


class MobileMessageConfigInput(BaseModel):
    username: str
    password: str
    sender: Optional[str] = ""
    enabled: bool = False


class LocantoMessagePayload(BaseModel):
    event: str
    sender: str
    adTitle: Optional[str] = "Locanto Ad"
    messageSnippet: str
    timestamp: str


# --- Bootcamp Simulator Schemas ---

class BootcampRunInput(BaseModel):
    personaIds: List[str]
    maxTurns: int = 5
    styleProfile: Dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_STYLE_PROFILE))


class BootcampControlInput(BaseModel):
    operation: str


class BootcampProfileInput(BaseModel):
    styleProfile: Dict[str, int]

# Backward-compatible alias
BootcampProfileApplyInput = BootcampProfileInput


class BootcampInformationRequestInput(BaseModel):
    information: str = Field(min_length=1, max_length=6000)

    @model_validator(mode="after")
    def clean_information(self):
        self.information = self.information.strip()
        if not self.information:
            raise ValueError("Information is required.")
        return self


__all__ = [
    "WebhookSMSInput",
    "AdminSmsSimulationInput",
    "TakeoverInput",
    "ReplyInput",
    "DraftUpdateInput",
    "MessageCorrectionInput",
    "NoteInput",
    "EscalateInput",
    "ResolveInput",
    "AutoresponderInput",
    "ThreadPinnedInput",
    "ThreadBlockedInput",
    "FirstContactAutoresponderInput",
    "InformationRequestResponseInput",
    "FirstContactAutoresponderAccountsInput",
    "ManualLearningInput",
    "LearnedInformationUpdateInput",
    "LearnedInformationBulkApproveInput",
    "SmsLearningPreviewInput",
    "SmsLearningCandidateInput",
    "SmsLearningImportInput",
    "ArrivalInviteInput",
    "ArrivalActivateInput",
    "ArrivalMessageInput",
    "PushSubscriptionKeysInput",
    "PushSubscriptionInput",
    "AdminLoginInput",
    "UpdateBookingInput",
    "ManualBookingInput",
    "BookingReminderInput",
    "SmsConfirmationInput",
    "BusinessVariableInput",
    "BusinessVariablesInput",
    "LineProfileInput",
    "LineProfilesInput",
    "LineAutoReplyInput",
    "SettingsUpdateInput",
    "QuickReplyInput",
    "OperationsChatInput",
    "OperationsVoiceToolInput",
    "OperationsRealtimeToolInput",
    "OperationsRealtimeTurnInput",
    "QARuleItem",
    "FileSaveInput",
    "FileSearchInput",
    "FilePurgeInput",
    "ServiceItem",
    "ServicesListInput",
    "ServiceAddOnItem",
    "ServiceAddOnsInput",
    "ServiceAddonItem",
    "ServiceAddonsInput",
    "WorkingHourEntry",
    "WorkingHoursInput",
    "AvailabilitySlotDay",
    "AvailabilityConfigInput",
    "MobileMessageConfigInput",
    "LocantoMessagePayload",
    "BootcampRunInput",
    "BootcampControlInput",
    "BootcampProfileInput",
    "BootcampProfileApplyInput",
    "BootcampInformationRequestInput",
]
