import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from github_oidc_service import GitHubOIDCError, GitHubOIDCVerifier


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def integer_b64url(value: int) -> str:
    return b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def __call__(self, request, timeout):
        self.calls += 1
        assert request.full_url == "https://token.actions.githubusercontent.com/.well-known/jwks"
        assert timeout == 10
        return FakeResponse(self.payload)


def signed_token(private_key, claims, *, kid="test-key"):
    header = {"alg": "RS256", "typ": "JWT", "kid": kid}
    encoded_header = b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_claims}.{b64url(signature)}"


def verifier_fixture():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    opener = FakeOpener({
        "keys": [{
            "kty": "RSA",
            "use": "sig",
            "kid": "test-key",
            "n": integer_b64url(public_numbers.n),
            "e": integer_b64url(public_numbers.e),
        }]
    })
    verifier = GitHubOIDCVerifier(opener=opener, now=lambda: 2_000_000_000)
    return private_key, opener, verifier


def valid_claims():
    return {
        "iss": "https://token.actions.githubusercontent.com",
        "sub": "repo:elle4a69/assistant-ui:ref:refs/heads/main",
        "aud": "assistant-ui-hub-operations",
        "iat": 1_999_999_900,
        "nbf": 1_999_999_900,
        "exp": 2_000_000_300,
        "jti": "unique-token-id",
        "repository": "elle4a69/assistant-ui",
    }


def test_verifies_signed_github_identity_and_caches_published_key():
    private_key, opener, verifier = verifier_fixture()
    token = signed_token(private_key, valid_claims())

    first = verifier.verify(token, audience="assistant-ui-hub-operations")
    second = verifier.verify(token, audience="assistant-ui-hub-operations")

    assert first["repository"] == "elle4a69/assistant-ui"
    assert second["jti"] == "unique-token-id"
    assert opener.calls == 1


def test_rejects_wrong_audience_expiry_and_tampered_signature():
    private_key, _opener, verifier = verifier_fixture()
    claims = valid_claims()
    token = signed_token(private_key, claims)

    with pytest.raises(GitHubOIDCError, match="audience"):
        verifier.verify(token, audience="wrong-audience")

    expired = {**claims, "iat": 1_999_998_000, "exp": 1_999_998_300}
    with pytest.raises(GitHubOIDCError, match="expired"):
        verifier.verify(signed_token(private_key, expired), audience="assistant-ui-hub-operations")

    prefix, payload, signature = token.split(".")
    tampered_payload = b64url(json.dumps({**claims, "repository": "attacker/repo"}).encode("utf-8"))
    with pytest.raises(GitHubOIDCError, match="invalid"):
        verifier.verify(f"{prefix}.{tampered_payload}.{signature}", audience="assistant-ui-hub-operations")
