"""Knowledge base and RAG retrieval service."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

try:
    from backend.core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from backend.core.state import KNOWLEDGE_CHUNKS
    from backend.core.clients import openai_client, get_line_profile, resolve_provider_context
    from backend.core.constants import APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES, TEMPLATE_VARIABLE_PATTERN
    from backend.curator.authority import (
        LEARNED_INFORMATION_FILENAME,
        normalize_knowledge_record,
        resolve_knowledge_authority,
    )
    from backend.curator.sanitizer import shared_knowledge_is_generic
    from backend.knowledge import render_template_variables
    from backend.services.settings_service import (
        get_line_business_variable_values,
        load_line_services,
    )
except ImportError:
    from core.config import DATA_DIR, KNOWLEDGE_DIR, PROMPTS_DIR
    from core.state import KNOWLEDGE_CHUNKS
    from core.clients import openai_client, get_line_profile, resolve_provider_context
    from core.constants import APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES, TEMPLATE_VARIABLE_PATTERN
    from curator.authority import (
        LEARNED_INFORMATION_FILENAME,
        normalize_knowledge_record,
        resolve_knowledge_authority,
    )
    from curator.sanitizer import shared_knowledge_is_generic
    from knowledge import render_template_variables
    from services.settings_service import (
        get_line_business_variable_values,
        load_line_services,
    )

logger = logging.getLogger(__name__)


def _dyn(name: str, fallback: Any = None) -> Any:
    """Resolve a symbol dynamically from sys.modules to support test monkeypatching."""
    import sys
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return fallback


def load_knowledge_base():
    global KNOWLEDGE_CHUNKS
    target_chunks = _dyn("KNOWLEDGE_CHUNKS", KNOWLEDGE_CHUNKS)
    target_chunks.clear()
    knowledge_dir = _dyn("KNOWLEDGE_DIR", KNOWLEDGE_DIR)
    if not os.path.exists(knowledge_dir):
        os.makedirs(knowledge_dir, exist_ok=True)
        if target_chunks is not KNOWLEDGE_CHUNKS:
            KNOWLEDGE_CHUNKS.clear()
        return
        
    for filename in os.listdir(knowledge_dir):
        filepath = os.path.join(knowledge_dir, filename)
        if not os.path.isfile(filepath):
            continue
            
        if filename.endswith(".txt"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
                    for index, chunk in enumerate(chunks):
                        target_chunks.append(normalize_knowledge_record(
                            {"text": chunk, "source_type": "uploaded_text"},
                            source=filename,
                            source_index=index,
                        ))
            except Exception as e:
                print(f"Error reading txt file {filename}: {e}")
                
        elif filename.endswith(".jsonl"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    for line_index, line in enumerate(f):
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                            
                            # Differentiate training few-shot examples
                            if "input" in obj and "output" in obj:
                                target_chunks.append({
                                    "source": filename,
                                    "type": "few_shot",
                                    "input": str(obj["input"]).strip(),
                                    "output": str(obj["output"]).strip(),
                                    "text": f"Input: {obj['input']}\nOutput: {obj['output']}"
                                })
                            else:
                                text_val = None
                                for key in ["text", "content", "question", "answer", "body"]:
                                    if key in obj:
                                        text_val = str(obj[key])
                                        break
                                if not text_val:
                                    text_val = " ".join(str(val) for val in obj.values() if isinstance(val, (str, int, float)))
                                if text_val:
                                    # Learned material is fail-closed until a staff member
                                    # has reviewed and approved it for retrieval.
                                    is_learned_entry = filename == LEARNED_INFORMATION_FILENAME
                                    # Uploaded JSONL is readable for audit, but it cannot
                                    # become customer-facing knowledge without an explicit
                                    # review marker, just like legacy uploaded text.
                                    review_status = str(obj.get("review_status", "pending"))
                                    normalized = normalize_knowledge_record(
                                        {**obj, "text": text_val.strip(), "review_status": review_status},
                                        source=filename,
                                        source_index=line_index,
                                        learned=is_learned_entry,
                                    )
                                    normalized["type"] = "text"
                                    normalized["category"] = str(obj.get("category", "internal_or_uncertain"))
                                    target_chunks.append(normalized)
                        except Exception as line_e:
                            print(f"Error parsing jsonl line: {line_e}")
            except Exception as e:
                print(f"Error reading jsonl file {filename}: {e}")
                
    if target_chunks is not KNOWLEDGE_CHUNKS:
        KNOWLEDGE_CHUNKS.clear()
        KNOWLEDGE_CHUNKS.extend(target_chunks)
    for mod_name in ("backend.main", "main"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "KNOWLEDGE_CHUNKS"):
            main_chunks = getattr(mod, "KNOWLEDGE_CHUNKS")
            if main_chunks is not target_chunks:
                main_chunks.clear()
                main_chunks.extend(target_chunks)
    print(f"Loaded {len(target_chunks)} knowledge chunks.")


def retrieve_knowledge_chunks(
    query: str,
    limit: int = 5,
    account_key: str = "primary",
) -> List[Dict[str, Any]]:
    chunks = _dyn("KNOWLEDGE_CHUNKS", KNOWLEDGE_CHUNKS)
    if not chunks:
        return []

    eligible_chunks = resolve_knowledge_authority(chunks, account_key=account_key)
    eligible_chunks = [chunk for chunk in eligible_chunks if chunk.get("type", "text") == "text"]
    if not eligible_chunks:
        return []
        
    query_words = [w.strip().lower() for w in query.split() if len(w.strip()) > 1]
    if not query_words:
        return eligible_chunks[:limit]
        
    scored_chunks = []
    for chunk in eligible_chunks:
        text_lower = chunk["text"].lower()
        score = sum(1 for word in query_words if word in text_lower)
        scored_chunks.append((score, chunk))
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    return [chunk for score, chunk in scored_chunks[:limit]]


def search_knowledge(query: str, limit: int = 5, account_key: str = "primary") -> str:
    results = retrieve_knowledge_chunks(query, limit, account_key)
    text_results = [r for r in results if r.get("type", "text") == "text"]
    
    if not text_results:
        text_results = results
        
    output_parts = []
    for res in text_results[:limit]:
        output_parts.append(f"[Source: {res['source']}]\n{res['text']}")
    return "\n\n".join(output_parts)


def validate_knowledge_template_variables(text: str) -> bool:
    """Accept only the explicit inert variables supported by reusable knowledge."""
    value = str(text or "")
    if "{{" in value or "}}" in value or "{%" in value or "${" in value:
        return False
    return all(match.group(1) in APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES
               for match in TEMPLATE_VARIABLE_PATTERN.finditer(value))


def resolve_knowledge_template(
    text: str,
    account_key: str,
    *,
    conversation_values: Optional[Dict[str, Any]] = None,
    booking_values: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve a reusable template from this provider only, or fail closed.

    Values supplied by a caller are accepted only for the current conversation
    and are checked against the selected provider catalogue where relevant.
    """
    text = re.sub(r"\{service\}", "{service_name}", str(text))
    text = re.sub(r"\{price\}", "{service_price}", text)
    text = re.sub(r"\{line_information_url\}", "{information_url}", text)
    if not validate_knowledge_template_variables(text):
        return None
    if account_key == "shared":
        return text if shared_knowledge_is_generic(text) and not TEMPLATE_VARIABLE_PATTERN.search(text) else None
    context = resolve_provider_context(account_key)
    values: Dict[str, Any] = {
        "provider_name": context["provider_name"],
        "information_url": context["information_url"],
    }
    fn_line_values = _dyn("get_line_business_variable_values", get_line_business_variable_values)
    line_values = fn_line_values(account_key)
    if line_values.get("line_information_url"):
        values["information_url"] = line_values["line_information_url"]
    # A location is a provider setting only when it is available for this line;
    # never fall back to a global setting which might belong to another line.
    fn_profile = _dyn("get_line_profile", get_line_profile)
    profile_location = str(fn_profile(account_key).get("providerLocation") or "").strip()
    if profile_location:
        values["provider_location"] = profile_location
    for source in (conversation_values or {}, booking_values or {}):
        if not isinstance(source, dict):
            return None
        for key in APPROVED_KNOWLEDGE_TEMPLATE_VARIABLES:
            if key in source and source[key] is not None:
                values[key] = str(source[key])

    service_name = values.get("service_name")
    if service_name:
        fn_services = _dyn("load_line_services", load_line_services)
        service = next((item for item in fn_services(account_key)
                        if str(item.get("name") or "") == service_name), None)
        if not service:
            return None
        values["service_price"] = str(service.get("price", ""))
        values["service_duration"] = str(service.get("duration", ""))
    elif "{service_name}" in text:
        # A generic reference is not a catalogue fact. It is safe only as
        # wording and cannot name, price, or borrow a service.
        values["service_name"] = "the relevant service"
    rendered = render_template_variables(str(text), values)
    if TEMPLATE_VARIABLE_PATTERN.search(rendered):
        return None
    return rendered


def match_qa_rule(message_text: str) -> Optional[str]:
    if not message_text:
        return None
    qa_path = os.path.join(DATA_DIR, "qa_rules.json")
    if os.path.exists(qa_path):
        try:
            with open(qa_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
                if isinstance(rules, list):
                    for rule in rules:
                        trigger = rule.get("trigger", "").strip().lower()
                        reply = rule.get("reply", "")
                        if trigger and trigger in message_text.lower():
                            return reply
        except Exception as e:
            print(f"Failed to read QA rules: {e}")
    return None


__all__ = [
    "load_knowledge_base",
    "retrieve_knowledge_chunks",
    "search_knowledge",
    "validate_knowledge_template_variables",
    "resolve_knowledge_template",
    "match_qa_rule",
]
