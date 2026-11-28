"""Minimal same-origin HTTP boundary, behind an operator-controlled TLS proxy.

No actor from headers/body, no static artifact directory, no worker endpoints.
The single-process loopback server is an engineering pilot, not an Internet edge.
"""
from dataclasses import asdict
from http.cookies import SimpleCookie, CookieError
from http.server import HTTPServer
from importlib.resources import files
import json
import re
import shutil
from urllib.parse import parse_qs, urlsplit

from montara_core.api.http import HealthHandler
from montara_core.application.identity import AuthError
from montara_core.application.ports import StorageError
from montara_core.domain.execution import Shot
from montara_core.domain.jobs import JobNotFound, TransitionError
from montara_core.domain.media import MediaNotFound
from montara_core.domain.projects import InvalidValue, ProjectNotFound, UnknownAccount


SESSION = "__Host-montara-session"
LOGIN = "__Host-montara-login"
MAX_BODY = 131072
ID = r"([0-9a-f-]{36})"


class RequestError(ValueError):
    pass


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise RequestError("duplicate field")
        result[k] = v
    return result


def create_authenticated_server(port, origin, core, auth, *, pilot=False):
    host = urlsplit(origin).netloc

    class Handler(HealthHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            policy = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                      "frame-ancestors 'none'; base-uri 'none'; form-action 'none'") if pilot else (
                      "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("Content-Security-Policy", policy)
            self.send_header("X-Frame-Options", "DENY")
            super().end_headers()

        def _cookie(self, name):
            headers = self.headers.get_all("Cookie", [])
            if len(headers) > 1:
                raise RequestError("ambiguous cookies")
            raw = headers[0] if headers else ""
            if len(raw) > 8192:
                raise RequestError("invalid cookies")
            # SimpleCookie alone silently accepts duplicate names.
            if sum(1 for part in raw.split(";") if part.strip().split("=", 1)[0] == name) > 1:
                raise RequestError("ambiguous cookies")
            try:
                parsed = SimpleCookie(raw)
                return parsed[name].value if name in parsed else None
            except CookieError:
                raise RequestError("invalid cookies") from None

        def _set_cookie(self, name, value, age):
            self.send_header("Set-Cookie", f"{name}={value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={age}")

        def _redirect(self, location, cookies=()):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            for cookie in cookies:
                self._set_cookie(*cookie)
            self.end_headers()

        def _body(self, keys, actor=None):
            if self.headers.get_all("Origin", []) != [origin]:
                raise AuthError("origin required")
            if (self.headers.get_all("Content-Type", []) != ["application/json"]
                    or self.headers.get_all("Transfer-Encoding", [])):
                raise RequestError("JSON required")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]):
                raise RequestError("invalid request length")
            size = int(lengths[0])
            if not 1 <= size <= MAX_BODY:
                raise RequestError("invalid request length")
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise RequestError("incomplete body")
            body = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(body, dict) or set(body) != set(keys):
                raise RequestError("invalid request fields")
            if pilot and actor is not None:
                auth.allow_mutation(actor)
            return body

        def _dispatch(self, method):
            if self.path == "/health" and method == "GET":
                return super().do_GET()
            if self.headers.get_all("Host", []) != [host]:
                raise RequestError("invalid host")
            if not self.path.startswith("/") or self.path.startswith("//") or len(self.path) > 8192:
                raise RequestError("invalid request target")
            target = urlsplit(self.path)
            if target.scheme or target.netloc or target.fragment:
                raise RequestError("invalid request target")
            path = target.path
            if target.query and path != "/auth/callback":
                raise RequestError("unexpected query")
            if pilot and method == "GET" and path in ("/", "/pilot.js", "/pilot.css"):
                name, content_type = {"/": ("pilot.html", "text/html"),
                    "/pilot.js": ("pilot.js", "application/javascript"),
                    "/pilot.css": ("pilot.css", "text/css")}[path]
                payload = files("montara_core.api").joinpath(name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type+"; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            if method == "GET" and path == "/auth/login":
                pending, url = auth.begin(self._cookie(LOGIN))
                return self._redirect(url, ((LOGIN, pending, auth.LOGIN_TTL),))
            if method == "GET" and path == "/auth/callback":
                query = parse_qs(target.query, keep_blank_values=True, strict_parsing=True, max_num_fields=10)
                # Google may include scope/authuser/prompt; never use those as identity.
                if any(len(v) != 1 for v in query.values()) or "error" in query:
                    raise AuthError("invalid authorization response")
                if "state" not in query or "code" not in query:
                    raise AuthError("invalid authorization response")
                session = auth.complete(self._cookie(LOGIN), query["state"][0],
                                        query["code"][0], self._cookie(SESSION))
                return self._redirect("/" if pilot else "/api/me", ((LOGIN, "", 0), (SESSION, session, auth.SESSION_TTL)))
            actor = auth.actor(self._cookie(SESSION))
            if method == "POST" and path == "/auth/logout":
                self._body(())
                auth.logout(self._cookie(SESSION))
                return self._redirect("/" if pilot else "/auth/login", ((SESSION, "", 0), (LOGIN, "", 0)))
            if method == "GET" and path == "/api/me":
                return self._json(200, {"account_id": actor})
            if path == "/api/projects":
                if method == "GET":
                    return self._json(200, [asdict(p) for p in core.projects.list_projects(actor)])
                if method == "POST":
                    body = self._body(("name",), actor)
                    return self._json(201, asdict(core.projects.create_project(actor, body["name"])))
            if method == "GET":
                if pilot:
                    match = re.fullmatch("/api/projects/"+ID+"/pilot", path)
                    if match:
                        return self._json(200, core.media.pilot_summary(actor, match[1]))
                for pattern, fn in (
                    ("/api/projects/"+ID, core.projects.get_project),
                    ("/api/representations/"+ID, core.media.get_representation),
                    ("/api/recipes/"+ID, core.media.get_recipe),
                    ("/api/jobs/"+ID, core.jobs.get_job),
                ):
                    match = re.fullmatch(pattern, path)
                    if match:
                        return self._json(200, asdict(fn(actor, match[1])))
                match = re.fullmatch("/api/jobs/"+ID+"/artifact", path)
                if match:
                    # Open/verify before sending headers. The opened descriptor
                    # is the one streamed; no path/locator disclosed.
                    with core.publication.open_artifact(actor, match[1]) as (receipt, stream):
                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp4")
                        self.send_header("Content-Length", str(receipt.size_bytes))
                        self.send_header("Content-Disposition", 'attachment; filename="montara.mp4"')
                        self.send_header("Cache-Control", "private, no-store")
                        self.end_headers()
                        try:
                            shutil.copyfileobj(stream, self.wfile, length=1024*1024)
                        except OSError:
                            self.close_connection = True
                    return
            if method == "POST":
                if pilot:
                    match = re.fullmatch("/api/projects/"+ID+"/fixture", path)
                    if match:
                        self._body((), actor)
                        return self._json(200, core.media.prepare_fixture(actor, match[1]))
                match = re.fullmatch("/api/projects/"+ID+"/media", path)
                if match:
                    body = self._body(("kind", "locator"), actor)
                    asset, representation, _ = core.media.register_original(
                        actor, match[1], body["kind"], body["locator"])
                    return self._json(201, {"asset": asdict(asset), "representation": asdict(representation)})
                match = re.fullmatch("/api/projects/"+ID+"/recipes", path)
                if match:
                    body = self._body(("expected_revision", "master", "shots"), actor)
                    if not isinstance(body["shots"], list) or not 1 <= len(body["shots"]) <= 1000:
                        raise RequestError("invalid shots")
                    shots = tuple(Shot(**s) for s in body["shots"])
                    recipe = core.media.save_recipe(actor, match[1], body["expected_revision"], body["master"], shots)
                    return self._json(201, asdict(recipe))
                match = re.fullmatch("/api/projects/"+ID+"/jobs", path)
                if match:
                    body = self._body(("key", "recipe_id"), actor)
                    if not isinstance(body["recipe_id"], str):
                        raise RequestError("export recipe required")
                    return self._json(201, asdict(core.jobs.create_job(
                        actor, match[1], body["key"], body["recipe_id"])))
            return self._json(404, {"error": "not_found"})

        def _handle(self, method):
            try:
                self._dispatch(method)
            except AuthError:
                self._json(401, {"error": "authentication_required"})
            except (ProjectNotFound, MediaNotFound, JobNotFound, UnknownAccount):
                self._json(404, {"error": "not_found"})
            except TransitionError:
                self._json(409, {"error": "conflict"})
            except (InvalidValue, ValueError, TypeError, KeyError, RecursionError):
                self._json(400, {"error": "invalid_request"})
            except StorageError:
                self._json(503, {"error": "storage_unavailable"})
            except OSError:
                self.close_connection = True

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def log_message(self, format, *args):
            pass

    return HTTPServer(("127.0.0.1", port), Handler)
