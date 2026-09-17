"""FastAPI Domain APIRouters package for assistant-ui backend.

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
