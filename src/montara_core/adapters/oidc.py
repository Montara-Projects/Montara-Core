"""Pinned HTTPS OIDC provider, RS256 verification through PyJWT.

No discovery from request input, redirects on the backchannel, access-token
authorization, or home-grown signature verification.
"""
import json
import os
from pathlib import Path
import secrets
import ssl
import stat
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler

from montara_core.application.identity import AuthError, OIDCSettings


def read_client_secret(path: Path):
    try:
        if path.resolve() != path:
            raise AuthError("OIDC secret file must be canonical")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise AuthError("OIDC secret file must be owned by service user, mode 0600")
            raw = stream.read(4097)
        secret = raw.decode("utf-8").rstrip("\r\n")
        if not 1 <= len(secret) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
            raise AuthError("invalid OIDC secret file")
        return secret
    except (OSError, UnicodeError):
        raise AuthError("OIDC secret file unavailable") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AuthError("OIDC backchannel redirect rejected")


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate key")
        result[k] = v
    return result


class OIDCProvider:
    def __init__(self, settings: OIDCSettings, client_secret: str, *, ssl_context=None):
        # Keep health-only wheel usable without optional extra.
        try:
            import jwt
            from jwt.algorithms import RSAAlgorithm
        except ImportError:
            raise AuthError("install the OIDC package extra") from None
        if not jwt.algorithms.has_crypto:
            raise AuthError("OIDC cryptographic backend unavailable")
        self._jwt = jwt
        self._rsa = RSAAlgorithm
        self.settings = settings
        self._secret = client_secret
        # Test-only dependency injection for a local CA; production uses system CA.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect(),
                                    HTTPSHandler(context=ssl_context or ssl.create_default_context()))

    def authorization_url(self, state, nonce, challenge):
        return self.settings.authorization_endpoint + "?" + urlencode({
            "client_id": self.settings.client_id, "response_type": "code",
            "scope": "openid", "redirect_uri": self.settings.redirect_uri,
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account",
        })

    def _json(self, url, data=None):
        try:
            if urlsplit(url).scheme != "https":
                raise AuthError("OIDC backchannel requires HTTPS")
            request = Request(url, data=data, headers={"Accept": "application/json",
                              **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
            with self._opener.open(request, timeout=10) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise AuthError("OIDC backchannel response rejected")
                body = response.read(262145)
            if len(body) > 262144:
                raise AuthError("OIDC response too large")
            result = json.loads(body, object_pairs_hook=_unique)
            if not isinstance(result, dict):
                raise AuthError("invalid OIDC response")
            return result
        except (OSError, ValueError, RecursionError):
            raise AuthError("OIDC backchannel failed") from None

    def exchange(self, code, verifier, nonce):
        response = self._json(self.settings.token_endpoint, urlencode({
            "grant_type": "authorization_code", "code": code,
            "client_id": self.settings.client_id, "client_secret": self._secret,
            "redirect_uri": self.settings.redirect_uri, "code_verifier": verifier,
        }).encode("ascii"))
        token = response.get("id_token")
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise AuthError("ID token missing or invalid")
        return self._verify(token, nonce)

    def _verify(self, token, nonce):
        jwt = self._jwt
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (header.get("alg") != "RS256" or not isinstance(kid, str)
                    or not 1 <= len(kid) <= 256 or "crit" in header
                    or any(k in header for k in ("jku", "jwk", "x5u"))):
                raise AuthError("ID token header rejected")
            keys = self._json(self.settings.jwks_uri).get("keys")
            if not isinstance(keys, list):
                raise AuthError("invalid provider key set")
            matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == kid]
            if len(matches) != 1:
                raise AuthError("unknown or ambiguous signing key")
            key = matches[0]
            if (key.get("kty") != "RSA" or key.get("use", "sig") != "sig"
                    or key.get("alg", "RS256") != "RS256"
                    or ("key_ops" in key and "verify" not in key["key_ops"])):
                raise AuthError("invalid signing key")
            public = self._rsa.from_jwk(json.dumps(key))
            if public.key_size < 2048:
                raise AuthError("signing key too small")
            claims = jwt.decode(token, public, algorithms=["RS256"], audience=self.settings.client_id,
                                issuer=self.settings.issuer,
                                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                                leeway=0)
            aud = claims["aud"]
            azp = claims.get("azp")
            if (("azp" in claims and azp != self.settings.client_id)
                    or (isinstance(aud, list) and len(aud) > 1 and azp != self.settings.client_id)
                    or type(claims["exp"]) is not int or type(claims["iat"]) is not int
                    or claims["iat"] < time.time() - 600
                    or claims["exp"] <= claims["iat"]
                    or not isinstance(claims["nonce"], str)
                    or not secrets.compare_digest(claims["nonce"], nonce)):
                raise AuthError("ID token claims rejected")
            return claims["iss"], claims["sub"]
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthError("ID token validation failed") from None
