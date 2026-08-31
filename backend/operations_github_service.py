"""Bounded GitHub transport for Operations AI cloud tasks.

The production application uses this client to verify GitHub-hosted queue
workers, request an immediate queue worker and inspect review branches.
Repository content writes are performed by short-lived GitHub Actions job
tokens, never by the production application.
This client never logs or returns its configured GitHub token.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request


DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 2_000_000
GITHUB_API_VERSION = "2022-11-28"
OPERATIONS_CODE_WORKFLOW = "operations-code.yml"


class OperationsGitHubError(RuntimeError):
    """A safe-to-report GitHub configuration or transport failure."""


@dataclass(frozen=True)
class _GitHubConfiguration:
    repository: str
    token: str


_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer)\s+\S+"),
    re.compile(r"(?i)\b(password|token|secret|api[_ -]?key)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:github_pat_|ghp_|gho_|sk-)[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bFlyV1\s+\S+", re.IGNORECASE),
)


def redact_sensitive_text(value: Any, *, limit: int = 12_000) -> str:
    """Remove common credential shapes before text reaches an LLM or log."""
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    if len(text) > limit:
        text = f"{text[:limit]}\n...[truncated]"
    return text


class OperationsGitHubClient:
    """Small authenticated GitHub REST client with strict, bounded methods."""

    def __init__(
        self,
        repository: Optional[str] = None,
        token: Optional[str] = None,
        *,
        opener: Optional[Callable[..., Any]] = None,
        api_url: str = "https://api.github.com",
    ) -> None:
        self._explicit_repository = repository
        self._explicit_token = token
        self._opener = opener or url_request.urlopen
        self._api_url = api_url.rstrip("/")

    def _configured_values(self) -> _GitHubConfiguration:
        repository = (
            self._explicit_repository
            if self._explicit_repository is not None
            else os.getenv("OPS_AGENT_GITHUB_REPO", "")
        )
        token = (
            self._explicit_token
            if self._explicit_token is not None
            else os.getenv("OPS_GITHUB_TOKEN", "")
        )
        return _GitHubConfiguration(str(repository or "").strip(), str(token or "").strip())

    @property
    def configured(self) -> bool:
        config = self._configured_values()
        return bool(config.token and _REPOSITORY_RE.fullmatch(config.repository))

    @property
    def repository(self) -> str:
        return self._configured_values().repository

    def _validated_configuration(self) -> _GitHubConfiguration:
        config = self._configured_values()
        if not _REPOSITORY_RE.fullmatch(config.repository):
            raise OperationsGitHubError("The Operations AI GitHub repository is not configured.")
        if not config.token:
            raise OperationsGitHubError("The Operations AI GitHub token is not configured.")
        return config

    @staticmethod
    def _safe_http_error(status_code: int) -> str:
        messages = {
            401: "GitHub rejected the Operations AI credentials.",
            403: "GitHub denied the Operations AI request.",
            404: "The requested GitHub repository resource was not found.",
            409: "GitHub rejected the request because the repository state changed.",
            422: "GitHub rejected the Operations AI request as invalid.",
            429: "GitHub temporarily rate-limited the Operations AI.",
        }
        return messages.get(status_code, f"GitHub returned HTTP {status_code}.")

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        config = self._validated_configuration()
        if not path.startswith("/"):
            raise OperationsGitHubError("The GitHub API path is invalid.")
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = url_request.Request(
            f"{self._api_url}{path}",
            data=body,
            method=method.upper(),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {config.token}",
                "User-Agent": "assistant-ui-operations-agent/2.0",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        bounded_timeout = max(2.0, min(float(timeout_seconds), 60.0))
        try:
            with self._opener(request, timeout=bounded_timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except url_error.HTTPError as exc:
            raise OperationsGitHubError(self._safe_http_error(exc.code)) from exc
        except (url_error.URLError, TimeoutError, OSError) as exc:
            raise OperationsGitHubError(
                f"GitHub could not be reached ({type(exc).__name__})."
            ) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OperationsGitHubError("The GitHub response exceeded its size limit.")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationsGitHubError("GitHub returned an invalid response.") from exc
        if not isinstance(value, dict):
            raise OperationsGitHubError("GitHub returned an unexpected response.")
        return value

    def dispatch_workflow(self) -> None:
        """Wake only the trusted Operations queue on main, without task inputs.

        Dispatch does not authorise coding or deployment: the worker must still
        authenticate and atomically claim an already-audited backend action.
        """
        repository = self._validated_configuration().repository
        self._request(
            "POST",
            f"/repos/{repository}/actions/workflows/{OPERATIONS_CODE_WORKFLOW}/dispatches",
            {"ref": "main"},
            timeout_seconds=5.0,
        )

    def list_workflow_runs(
        self,
        *,
        limit: int = 20,
        workflow: Optional[str] = OPERATIONS_CODE_WORKFLOW,
        event: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        bounded_limit = max(1, min(100, int(limit)))
        repository = self._validated_configuration().repository
        if workflow:
            workflow_value = url_parse.quote(workflow, safe="")
            path = f"/repos/{repository}/actions/workflows/{workflow_value}/runs"
        else:
            path = f"/repos/{repository}/actions/runs"
        query: Dict[str, str] = {"per_page": str(bounded_limit)}
        if event:
            query["event"] = event
        response = self._request("GET", f"{path}?{url_parse.urlencode(query)}")
        runs = response.get("workflow_runs", [])
        return [item for item in runs[:bounded_limit] if isinstance(item, dict)] if isinstance(runs, list) else []

    def get_workflow_run(self, run_id: int) -> Dict[str, Any]:
        try:
            bounded_run_id = int(run_id)
        except (TypeError, ValueError) as exc:
            raise OperationsGitHubError("The GitHub workflow run ID is invalid.") from exc
        if bounded_run_id <= 0:
            raise OperationsGitHubError("The GitHub workflow run ID is invalid.")
        repository = self._validated_configuration().repository
        return self._request("GET", f"/repos/{repository}/actions/runs/{bounded_run_id}")

    def read_file(self, path: str, *, ref: str = "main") -> Dict[str, Any]:
        repository = self._validated_configuration().repository
        encoded_path = url_parse.quote(path, safe="/")
        query = url_parse.urlencode({"ref": ref})
        response = self._request("GET", f"/repos/{repository}/contents/{encoded_path}?{query}")
        if response.get("type") != "file" or response.get("encoding") != "base64":
            raise OperationsGitHubError("GitHub did not return a readable source file.")
        try:
            content = base64.b64decode(str(response.get("content") or ""), validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise OperationsGitHubError("The GitHub source file is not valid UTF-8 text.") from exc
        return {
            "path": str(response.get("path") or path),
            "sha": str(response.get("sha") or ""),
            "size": int(response.get("size") or len(content.encode("utf-8"))),
            "content": content,
        }

    def get_branch(self, branch: str) -> Dict[str, Any]:
        repository = self._validated_configuration().repository
        encoded_branch = url_parse.quote(branch, safe="")
        return self._request("GET", f"/repos/{repository}/branches/{encoded_branch}")

    def compare(self, base: str, head: str) -> Dict[str, Any]:
        repository = self._validated_configuration().repository
        encoded_base = url_parse.quote(base, safe="")
        encoded_head = url_parse.quote(head, safe="")
        return self._request(
            "GET",
            f"/repos/{repository}/compare/{encoded_base}...{encoded_head}?per_page=100",
        )

    def get_git_commit(self, sha: str) -> Dict[str, Any]:
        if not _COMMIT_SHA_RE.fullmatch(sha):
            raise OperationsGitHubError("The Git commit ID is invalid.")
        repository = self._validated_configuration().repository
        return self._request("GET", f"/repos/{repository}/git/commits/{sha.casefold()}")

    def get_ref(self, ref: str) -> Dict[str, Any]:
        repository = self._validated_configuration().repository
        encoded_ref = url_parse.quote(ref.strip("/"), safe="/")
        return self._request("GET", f"/repos/{repository}/git/ref/{encoded_ref}")
