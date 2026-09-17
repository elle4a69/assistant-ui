"""Compatibility helper to bridge dynamic state between backend.curator and backend.main."""

import sys
import threading
from typing import Any

DEFAULT_LEARNED_INFORMATION_LOCK = threading.Lock()


def get_main_module() -> Any:
    """Return the main application module (either 'main' or 'backend.main')."""
    main_mod = sys.modules.get("__main__")
    if main_mod is not None and (hasattr(main_mod, "app") or hasattr(main_mod, "init_db")):
        return sys.modules["__main__"]
    mod = sys.modules.get("main") or sys.modules.get("backend.main")
    if mod is None:
        try:
            import backend.main as mod
        except ImportError:
            try:
                import main as mod
            except ImportError:
                return None
    return mod


def get_main_attr(name: str, fallback: Any = None) -> Any:
    """Retrieve an attribute from main dynamically, or return fallback."""
    mod = get_main_module()
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    if fallback is not None:
        return fallback
    for mod_path in (
        "backend.core.config",
        "backend.core.state",
        "backend.core.clients",
        "backend.services.learning_service",
    ):
        target = sys.modules.get(mod_path)
        if target is not None and hasattr(target, name):
            return getattr(target, name)
    return fallback


def export_to_main(names_or_dict: Any, target_mod: Any = None) -> None:
    """Explicitly export attributes or mappings to the main application module."""
    if target_mod is None:
        target_mod = get_main_module()
    if target_mod is None:
        return
    if isinstance(names_or_dict, dict):
        for k, v in names_or_dict.items():
            setattr(target_mod, k, v)
    elif isinstance(names_or_dict, (list, tuple, set)):
        import backend.curator as curator
        for name in names_or_dict:
            if hasattr(curator, name):
                setattr(target_mod, name, getattr(curator, name))


def wire_curator_to_main(target_mod: Any = None) -> None:
    """Wire curator exports and delegations onto the target main module."""
    if target_mod is None:
        target_mod = get_main_module()
    if target_mod is None:
        return
    import backend.curator as curator
    for symbol in curator.__all__:
        if hasattr(curator, symbol):
            setattr(target_mod, symbol, getattr(curator, symbol))

