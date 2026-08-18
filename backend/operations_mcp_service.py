"""Small authenticated MCP-over-SSE client for the owner's coding bridge.

The production app uses this client only with the server URL and bearer token
supplied through environment variables.  Tool names and arguments remain
allowlisted by ``main.py``; this module is deliberately transport-only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urljoin, urlparse


MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_SSE_BYTES = 1_000_000


class OperationsMCPError(RuntimeError):
    """A bounded, safe-to-report coding bridge failure."""


@dataclass(frozen=True)
class _MCPConfiguration:
    sse_url: str
    bearer_token: str


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer)\s+\S+"),
    re.compile(r"(?i)\b(password|token|secret|api[_ -]?key)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:github_pat_|ghp_|gho_|sk-)[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bFlyV1\s+\S+", re.IGNORECASE),
)


def redact_sensitive_text(value: Any, *, limit: int = 12_000) -> str:
    """Remove common credential shapes before a bridge result reaches an LLM."""
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    if len(text) > limit:
        text = f"{text[:limit]}\n...[truncated]"
    return text


def mcp_result_text(result: Dict[str, Any], *, limit: int = 12_000) -> str:
    """Flatten text content from an MCP tools/call result."""
    parts = []
    for item in result.get("content", []) if isinstance(result, dict) else []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return redact_sensitive_text("\n".join(parts), limit=limit)


def mcp_result_value(result: Dict[str, Any], *, limit: int = 12_000) -> Any:
    """Decode a JSON text result when possible, otherwise return bounded text."""
    text = mcp_result_text(result, limit=limit)
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text


class OperationsMCPClient:
    """Open a short-lived authenticated SSE session for one MCP tool call."""

    def __init__(
        self,
        sse_url: Optional[str] = None,
        bearer_token: Optional[str] = None,
        *,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._explicit_sse_url = sse_url
        self._explicit_bearer_token = bearer_token
        self._opener = opener or url_request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self._configured_values().sse_url and self._configured_values().bearer_token)

    def _configured_values(self) -> _MCPConfiguration:
        sse_url = (
            self._explicit_sse_url
            if self._explicit_sse_url is not None
            else os.getenv("OPS_AGENT_SSE_URL", "")
        )
        bearer_token = (
            self._explicit_bearer_token
            if self._explicit_bearer_token is not None
            else os.getenv("OPS_AGENT_BEARER_TOKEN", "")
        )
        return _MCPConfiguration(str(sse_url or "").strip(), str(bearer_token or "").strip())

    def _validated_configuration(self) -> _MCPConfiguration:
        config = self._configured_values()
        if not config.sse_url or not config.bearer_token:
            raise OperationsMCPError("The coding bridge is not configured.")
        parsed = urlparse(config.sse_url)
        is_local = (parsed.hostname or "").casefold() in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in ({"http", "https"} if is_local else {"https"}):
            raise OperationsMCPError("The coding bridge URL must use HTTPS.")
        if not parsed.hostname:
            raise OperationsMCPError("The coding bridge URL is invalid.")
        return config

    @staticmethod
    def _origin(url: str) -> tuple[str, str, Optional[int]]:
        parsed = urlparse(url)
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port

    @staticmethod
    def _headers(config: _MCPConfiguration, *, content_type: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {config.bearer_token}",
            "User-Agent": "assistant-ui-operations-agent/1.0",
            "ngrok-skip-browser-warning": "true",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _post_json(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        config: _MCPConfiguration,
        timeout_seconds: float,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = url_request.Request(
            endpoint,
            data=body,
            method="POST",
            headers=self._headers(config, content_type="application/json"),
        )
        with self._opener(request, timeout=timeout_seconds) as response:
            response.read(16_384)

    @staticmethod
    def _read_data_line(stream: Any, byte_budget: list[int]) -> str:
        while True:
            raw_line = stream.readline()
            if not raw_line:
                raise OperationsMCPError("The coding bridge closed the event stream unexpectedly.")
            if isinstance(raw_line, str):
                raw_bytes = raw_line.encode("utf-8", errors="replace")
                line = raw_line
            else:
                raw_bytes = raw_line
                line = raw_line.decode("utf-8", errors="replace")
            byte_budget[0] += len(raw_bytes)
            if byte_budget[0] > MAX_SSE_BYTES:
                raise OperationsMCPError("The coding bridge response exceeded its size limit.")
            line = line.strip()
            if line.startswith("data:"):
                return line[5:].strip()

    def _read_endpoint(self, stream: Any, config: _MCPConfiguration, byte_budget: list[int]) -> str:
        endpoint_value = self._read_data_line(stream, byte_budget)
        endpoint = urljoin(config.sse_url, endpoint_value)
        if self._origin(endpoint) != self._origin(config.sse_url):
            raise OperationsMCPError("The coding bridge returned an unsafe message endpoint.")
        return endpoint

    def _read_response(self, stream: Any, request_id: int, byte_budget: list[int]) -> Dict[str, Any]:
        while True:
            raw_payload = self._read_data_line(stream, byte_budget)
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("id") != request_id:
                continue
            if payload.get("error"):
                error_value = payload.get("error")
                if isinstance(error_value, dict):
                    message = error_value.get("message") or "The coding bridge rejected the request."
                else:
                    message = "The coding bridge rejected the request."
                raise OperationsMCPError(redact_sensitive_text(message, limit=500))
            result = payload.get("result", {})
            return result if isinstance(result, dict) else {"content": []}

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Initialize one MCP session, execute one tool, and return its result."""
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise OperationsMCPError("The coding bridge tool name is invalid.")
        if not isinstance(arguments or {}, dict):
            raise OperationsMCPError("The coding bridge tool arguments are invalid.")
        bounded_timeout = max(2.0, min(float(timeout_seconds), 3_700.0))
        config = self._validated_configuration()
        request = url_request.Request(
            config.sse_url,
            method="GET",
            headers=self._headers(config),
        )
        byte_budget = [0]
        try:
            with self._opener(request, timeout=bounded_timeout) as stream:
                endpoint = self._read_endpoint(stream, config, byte_budget)
                self._post_json(
                    endpoint,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "assistant-ui", "version": "1.0"},
                        },
                    },
                    config,
                    bounded_timeout,
                )
                self._read_response(stream, 1, byte_budget)
                self._post_json(
                    endpoint,
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    config,
                    bounded_timeout,
                )
                self._post_json(
                    endpoint,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": name.strip(), "arguments": arguments or {}},
                    },
                    config,
                    bounded_timeout,
                )
                return self._read_response(stream, 2, byte_budget)
        except OperationsMCPError:
            raise
        except url_error.HTTPError as exc:
            raise OperationsMCPError(f"The coding bridge returned HTTP {exc.code}.") from exc
        except (url_error.URLError, TimeoutError, OSError) as exc:
            raise OperationsMCPError(
                f"The coding bridge could not be reached ({type(exc).__name__})."
            ) from exc
