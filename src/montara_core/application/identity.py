"""OIDC browser ceremony and bounded opaque server-side sessions."""
from dataclasses import dataclass
from collections import deque
import base64
import hashlib
from pathlib import Path
import secrets
import threading
import time
from typing import Protocol


class AuthError(RuntimeError):
    """Authentication rejected, without provider/token details."""


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_id: str
    client_secret_file: Path
    public_origin: str

    @property
    def redirect_uri(self):
        return self.public_origin + "/auth/callback"


class IdentityStore(Protocol):
    def resolve_identity(self, issuer: str, subject: str) -> str: ...


class Provider(Protocol):
    def authorization_url(self, state: str, nonce: str, challenge: str) -> str: ...
    def exchange(self, code: str, verifier: str, nonce: str) -> tuple[str, str]: ...


@dataclass(frozen=True)
class Pending:
    state: str
    nonce: str
    verifier: str
    expires: float


@dataclass(frozen=True)
class Session:
    account: str
    expires: float


class AuthenticationService:
    LOGIN_TTL = 300
    SESSION_TTL = 3600
    CAPACITY = 1024

    def __init__(self, identities: IdentityStore, provider: Provider, *, admitted=None):
        self._identities = identities
        self._provider = provider
        self._admitted = frozenset(admitted) if admitted is not None else None
        self._pending = {}
        self._sessions = {}
        self._lock = threading.Lock()
        self._login_events = deque()
        self._mutation_events = {}

    @staticmethod
    def _token():
        return secrets.token_urlsafe(32)

    @staticmethod
    def _key(token):
        if not isinstance(token, str) or not 1 <= len(token) <= 128:
            return ""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _prune(self):
        now = time.monotonic()
        for table in (self._pending, self._sessions):
            for key in list(table):
                if table[key].expires <= now:
                    del table[key]

    @staticmethod
    def _rate(events, limit):
        now = time.monotonic()
        while events and events[0] <= now - 60:
            events.popleft()
        if len(events) >= limit:
            raise AuthError("pilot rate limit reached")
        events.append(now)

    def allow_mutation(self, actor):
        if self._admitted is not None:
            with self._lock:
                self._rate(self._mutation_events.setdefault(actor, deque()), 30)

    def begin(self, previous=None):
        browser, state, nonce, verifier = (self._token() for _ in range(4))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        url = self._provider.authorization_url(state, nonce, challenge)
        with self._lock:
            self._prune()
            self._pending.pop(self._key(previous), None)
            if self._admitted is not None:
                self._rate(self._login_events, 30)
            if len(self._pending) >= self.CAPACITY:
                raise AuthError("login capacity reached")
            self._pending[self._key(browser)] = Pending(state, nonce, verifier, time.monotonic()+self.LOGIN_TTL)
        return browser, url

    def complete(self, browser, state, code, old_session=None):
        with self._lock:
            self._prune()
            pending = self._pending.pop(self._key(browser), None)
        # Consume once, including a failed callback. No code exchange for bad state.
        if (pending is None or not isinstance(state, str) or len(state) > 128
                or not secrets.compare_digest(pending.state, state)
                or not isinstance(code, str) or not 1 <= len(code) <= 4096):
            raise AuthError("invalid login transaction")
        issuer, subject = self._provider.exchange(code, pending.verifier, pending.nonce)
        if (not isinstance(issuer, str) or not issuer or len(issuer) > 2048
                or not isinstance(subject, str) or not 1 <= len(subject) <= 255
                or any(ord(c) < 33 or ord(c) > 126 for c in subject)):
            raise AuthError("invalid verified identity")
        if self._admitted is not None and (issuer, subject) not in self._admitted:
            raise AuthError("identity not admitted")
        account = self._identities.resolve_identity(issuer, subject)
        token = self._token()
        with self._lock:
            self._prune()
            self._sessions.pop(self._key(old_session), None)
            if len(self._sessions) >= self.CAPACITY:
                raise AuthError("session capacity reached")
            self._sessions[self._key(token)] = Session(account, time.monotonic()+self.SESSION_TTL)
        return token

    def actor(self, token):
        with self._lock:
            self._prune()
            session = self._sessions.get(self._key(token))
            if session is None:
                raise AuthError("authentication required")
            return session.account

    def logout(self, token):
        with self._lock:
            self._sessions.pop(self._key(token), None)
