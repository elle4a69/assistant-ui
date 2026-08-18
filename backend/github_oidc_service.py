"""Verification for GitHub Actions OIDC identities used by cloud coding jobs."""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any, Callable, Dict, Optional
from urllib import error as url_error
from urllib import request as url_request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
MAX_JWKS_BYTES = 500_000


class GitHubOIDCError(RuntimeError):
    """A deliberately generic OIDC validation failure."""


def _decode_base64url(value: str) -> bytes:
    if not value or len(value) > 100_000:
        raise GitHubOIDCError("The GitHub worker identity is invalid.")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise GitHubOIDCError("The GitHub worker identity is invalid.") from exc


class GitHubOIDCVerifier:
    """Validate a short-lived RS256 token against GitHub's published keys."""

    def __init__(
        self,
        *,
        opener: Optional[Callable[..., Any]] = None,
        now: Optional[Callable[[], float]] = None,
        key_cache_seconds: int = 3_600,
    ) -> None:
        self._opener = opener or url_request.urlopen
        self._now = now or time.time
        self._key_cache_seconds = max(60, min(int(key_cache_seconds), 86_400))
        self._keys: Dict[str, rsa.RSAPublicKey] = {}
        self._keys_expire_at = 0.0
        self._lock = threading.Lock()

    def _fetch_keys(self) -> Dict[str, rsa.RSAPublicKey]:
        request = url_request.Request(
            GITHUB_OIDC_JWKS_URL,
            headers={"Accept": "application/json", "User-Agent": "assistant-ui-operations-agent/2.0"},
        )
        try:
            with self._opener(request, timeout=10) as response:
                raw = response.read(MAX_JWKS_BYTES + 1)
        except (url_error.HTTPError, url_error.URLError, TimeoutError, OSError) as exc:
            raise GitHubOIDCError("GitHub worker identity verification is temporarily unavailable.") from exc
        if len(raw) > MAX_JWKS_BYTES:
            raise GitHubOIDCError("GitHub worker identity verification is temporarily unavailable.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubOIDCError("GitHub worker identity verification is temporarily unavailable.") from exc
        values = payload.get("keys", []) if isinstance(payload, dict) else []
        keys: Dict[str, rsa.RSAPublicKey] = {}
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict) or item.get("kty") != "RSA" or item.get("use") not in {None, "sig"}:
                continue
            kid = str(item.get("kid") or "")
            try:
                modulus = int.from_bytes(_decode_base64url(str(item.get("n") or "")), "big")
                exponent = int.from_bytes(_decode_base64url(str(item.get("e") or "")), "big")
                if not kid or modulus.bit_length() < 2_048 or exponent < 3:
                    continue
                keys[kid] = rsa.RSAPublicNumbers(exponent, modulus).public_key()
            except (ValueError, GitHubOIDCError):
                continue
        if not keys:
            raise GitHubOIDCError("GitHub worker identity verification is temporarily unavailable.")
        return keys

    def _key_for(self, kid: str) -> rsa.RSAPublicKey:
        now = self._now()
        with self._lock:
            if now >= self._keys_expire_at or kid not in self._keys:
                self._keys = self._fetch_keys()
                self._keys_expire_at = now + self._key_cache_seconds
            key = self._keys.get(kid)
        if key is None:
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        return key

    def verify(self, token: str, *, audience: str) -> Dict[str, Any]:
        if not token or len(token) > 20_000 or not audience:
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        parts = token.split(".")
        if len(parts) != 3:
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        try:
            header = json.loads(_decode_base64url(parts[0]).decode("utf-8"))
            claims = json.loads(_decode_base64url(parts[1]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubOIDCError("The GitHub worker identity is invalid.") from exc
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        if header.get("alg") != "RS256" or header.get("typ") not in {None, "JWT"}:
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        kid = str(header.get("kid") or "")
        key = self._key_for(kid)
        try:
            key.verify(
                _decode_base64url(parts[2]),
                f"{parts[0]}.{parts[1]}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, UnicodeEncodeError) as exc:
            raise GitHubOIDCError("The GitHub worker identity is invalid.") from exc

        now = self._now()
        try:
            issued_at = float(claims["iat"])
            not_before = float(claims.get("nbf", issued_at))
            expires_at = float(claims["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubOIDCError("The GitHub worker identity is invalid.") from exc
        if (
            claims.get("iss") != GITHUB_OIDC_ISSUER
            or issued_at > now + 60
            or not_before > now + 60
            or expires_at <= now - 30
            or expires_at - issued_at > 900
            or now - issued_at > 900
        ):
            raise GitHubOIDCError("The GitHub worker identity is invalid or expired.")
        audiences = claims.get("aud")
        if isinstance(audiences, str):
            audiences = [audiences]
        if not isinstance(audiences, list) or audience not in audiences:
            raise GitHubOIDCError("The GitHub worker identity has the wrong audience.")
        if not str(claims.get("sub") or "") or not str(claims.get("jti") or ""):
            raise GitHubOIDCError("The GitHub worker identity is invalid.")
        return claims
