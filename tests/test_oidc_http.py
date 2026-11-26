"""Real HTTPS test IdP, RSA signatures and HTTP owner-scoped integration.

The IdP is test-only. No Google credentials, TLS proxy or real render assumed.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from montara_core.adapters.oidc import OIDCProvider, read_client_secret
from montara_core.adapters.sqlite_db import SQLiteDatabase, _SCHEMA_5
from montara_core.api.authenticated import create_authenticated_server, LOGIN, SESSION
from montara_core.application.identity import AuthenticationService, AuthError, OIDCSettings
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import Shot
from montara_core.domain.jobs import JobState
from test_publication import candidate_for


class TestIdP:
    """HTTPS token/JWKS endpoints; issued codes are one-use and PKCE-bound."""
    def __init__(self, root):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = {**json.loads(RSAAlgorithm.to_jwk(self.key.public_key())),
                    "kid": "test-key", "alg": "RS256", "use": "sig"}
        self.keys = [self.jwk]
        self.codes = {}
        self.exchanges = 0
        self.secret = secrets.token_urlsafe(32)
        self.client = "engineering-test-client"
        self.origin = "https://montara.test"
        self.browser_enabled = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, value):
                payload = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if urlsplit(self.path).path == "/authorize" and owner.browser_enabled:
                    # Test-only browser account selection; no real credentials.
                    from http.cookies import SimpleCookie
                    cookies = SimpleCookie(self.headers.get("Cookie", ""))
                    subject = cookies.get("fixture-subject")
                    if subject is None:
                        return self.reply(400, {"error":"fixture_subject_required"})
                    state, code = owner.issue(owner.issuer+self.path, subject.value)
                    self.send_response(303)
                    self.send_header("Location", owner.origin+"/auth/callback?"+urlencode({"state":state,"code":code}))
                    self.end_headers()
                    return
                if self.path == "/jwks":
                    return self.reply(200, {"keys": owner.keys})
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", owner.issuer + "/jwks")
                    self.end_headers()
                    return
                self.reply(404, {})

            def do_POST(self):
                if self.path != "/token":
                    return self.reply(404, {})
                owner.exchanges += 1
                fields = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                record = owner.codes.pop(fields.get("code", [""])[0], None)
                verifier = fields.get("code_verifier", [""])[0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if (record is None or challenge != record[0] or
                        fields.get("client_id") != [owner.client] or
                        fields.get("client_secret") != [owner.secret] or
                        fields.get("redirect_uri") != [owner.origin+"/auth/callback"] or
                        fields.get("grant_type") != ["authorization_code"]):
                    return self.reply(400, {"error":"invalid_grant"})
                self.reply(200, {"id_token": record[1], "access_token":"unused"})

            def log_message(self, *args):
                pass

        cert, key = root/"test-ca.pem", root/"test-key.pem"
        subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes",
                        "-keyout",str(key),"-out",str(cert),"-days","1",
                        "-subj","/CN=localhost","-addext","subjectAltName=IP:127.0.0.1,DNS:localhost"],
                       check=True, capture_output=True, timeout=30)
        self.server = HTTPServer(("127.0.0.1",0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.issuer = "https://127.0.0.1:"+str(self.server.server_port)
        self.context = ssl.create_default_context(cafile=str(cert))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def settings(self, secret_file):
        return OIDCSettings(self.issuer, self.issuer+"/authorize", self.issuer+"/token",
                            self.issuer+"/jwks", self.client, secret_file, self.origin)

    def issue(self, url, subject, *, claims=None, wrong_key=False, headers=None, algorithm="RS256"):
        query = parse_qs(urlsplit(url).query)
        assert query["response_type"]==["code"]
        assert query["scope"]==["openid"]
        assert query["code_challenge_method"]==["S256"]
        assert query["redirect_uri"]==[self.origin+"/auth/callback"]
        now = int(time.time())
        payload = {"iss":self.issuer,"sub":subject,"aud":self.client,"iat":now,
                   "exp":now+300,"nonce":query["nonce"][0],"email":"shared@example.invalid"}
        payload.update(claims or {})
        signing_key = self.other_key if wrong_key else self.key
        if algorithm=="HS256":
            signing_key = "fixture-secret"*3
        token = jwt.encode(payload, signing_key, algorithm=algorithm,
                           headers=headers or {"kid":"test-key"})
        code = secrets.token_urlsafe(24)
        self.codes[code]=(query["code_challenge"][0],token)
        return query["state"][0],code


class OIDCHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.idp = TestIdP(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.idp.close()
        cls.tmp.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.originals, self.workspaces, self.artifacts = [self.root/n for n in
                                                          ("originals","workspaces","artifacts")]
        for p in (self.originals,self.workspaces,self.artifacts):
            p.mkdir()
        self.secret = self.root/"client-secret"
        self.secret.write_text(self.idp.secret)
        self.secret.chmod(0o600)
        self.settings = self.idp.settings(self.secret)
        self.config = Config(sqlite_path=self.root/"core.db",media_root=self.originals,
                             workspace_root=self.workspaces,artifact_root=self.artifacts,
                             oidc=self.settings)
        self.core = build_core(self.config)
        self.provider = OIDCProvider(self.settings,read_client_secret(self.secret),ssl_context=self.idp.context)
        self.auth = AuthenticationService(self.core.identity,self.provider)
        self.server = create_authenticated_server(0,self.settings.public_origin,self.core,self.auth)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, *, cookie=None, body=None, origin=True, headers=None):
        h = {"Host":"montara.test", **(headers or {})}
        if cookie is not None:
            h["Cookie"]=cookie
        if body is not None:
            h["Content-Type"]="application/json"
            if origin:
                h["Origin"]=self.settings.public_origin
            body=json.dumps(body).encode()
        conn=HTTPConnection("127.0.0.1",self.server.server_port,timeout=15)
        try:
            conn.request(method,path,body=body,headers=h)
            res=conn.getresponse()
            return res.status,res.headers,res.read()
        finally:
            conn.close()

    def start(self):
        status,h,_=self.request("GET","/auth/login")
        self.assertEqual(status,303)
        cookie=h.get("Set-Cookie")
        for attr in ("Secure","HttpOnly","SameSite=Lax","Path=/","Max-Age=300"):
            self.assertIn(attr,cookie)
        self.assertIn("no-store",h["Cache-Control"])
        return cookie.split(";",1)[0], h["Location"]

    def callback(self, cookie, state, code):
        return self.request("GET","/auth/callback?"+urlencode({"state":state,"code":code}),cookie=cookie)

    def login(self, subject):
        browser,url=self.start()
        state,code=self.idp.issue(url,subject)
        status,h,_=self.callback(browser,state,code)
        self.assertEqual(status,303)
        self.assertEqual(h["Location"],"/api/me")
        session=next(c for c in h.get_all("Set-Cookie") if c.startswith(SESSION+"="))
        for attr in ("Secure","HttpOnly","SameSite=Lax","Path=/","Max-Age=3600"):
            self.assertIn(attr,session)
        cookie=session.split(";",1)[0]
        return cookie,self.get(cookie,"/api/me")["account_id"]

    def get(self,cookie,path,status=200):
        actual,_,raw=self.request("GET",path,cookie=cookie)
        self.assertEqual(actual,status,raw)
        return json.loads(raw)

    def post(self,cookie,path,body,status=201):
        actual,_,raw=self.request("POST",path,cookie=cookie,body=body)
        self.assertEqual(actual,status,raw)
        return json.loads(raw)

    def test_real_oidc_same_email_distinct_subjects_and_relogin(self):
        a,aid=self.login("subject-a")
        b,bid=self.login("subject-b")
        self.assertNotEqual(aid,bid)
        self.assertNotEqual(a,b)
        again,aid2=self.login("subject-a")
        self.assertEqual(aid,aid2)
        self.assertNotEqual(a,again)
        reopened=build_core(self.config)
        self.assertEqual(reopened.identity.resolve_identity(self.idp.issuer,"subject-a"),aid)
        self.assertNotEqual(reopened.identity.resolve_identity("https://other.invalid","subject-a"),aid)
        with SQLiteDatabase(self.config.sqlite_path)._connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM accounts").fetchone(),(3,))
            self.assertEqual(db.execute("SELECT count(*) FROM oidc_identities").fetchone(),(3,))
        # No linking to an existing internal synthetic account, even if claimed.
        preexisting=self.core.projects.provision_account().id
        _,new_id=self.login(preexisting)
        self.assertNotEqual(preexisting,new_id)

    def test_signature_issuer_audience_expiry_nonce_and_key_negatives(self):
        now=int(time.time())
        for claims,options in [
            ({"iss":"https://wrong.invalid"},{}),
            ({"aud":"wrong-client"},{}),
            ({"aud":[self.idp.client,"other"]},{}),
            ({"azp":"wrong-client"},{}),
            ({"exp":now-1},{}),
            ({"iat":now+3600},{}),
            ({"nonce":"wrong"},{}),
            ({"sub":""},{}),
            ({},{"wrong_key":True}),
            ({},{"headers":{"kid":"unknown"}}),
            ({},{"algorithm":"HS256"}),
            ({},{"headers":{"kid":"test-key","jku":"https://untrusted.invalid"}}),
        ]:
            with self.subTest(claims=claims,options=options):
                browser,url=self.start()
                state,code=self.idp.issue(url,"bad-subject",claims=claims,**options)
                self.assertEqual(self.callback(browser,state,code)[0],401)
        with SQLiteDatabase(self.config.sqlite_path)._connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM oidc_identities").fetchone(),(0,))

    def test_state_browser_binding_replay_and_pkce(self):
        browser,url=self.start()
        state,code=self.idp.issue(url,"state-subject")
        count=self.idp.exchanges
        self.assertEqual(self.callback(None,state,code)[0],401)
        self.assertEqual(self.callback(browser,"wrong",code)[0],401)
        self.assertEqual(self.callback(browser,state,code)[0],401)
        self.assertEqual(self.idp.exchanges,count)
        first,url1=self.start()
        second,url2=self.start()
        s1,c1=self.idp.issue(url1,"first")
        s2,c2=self.idp.issue(url2,"second")
        self.assertEqual(self.callback(first,s1,c2)[0],401) # actual IdP rejects verifier
        self.assertEqual(self.callback(second,s2,c2)[0],401) # one-use code consumed
        third,url3=self.start()
        s3,c3=self.idp.issue(url3,"third")
        self.assertEqual(self.callback(third,s3,c3)[0],303)
        count=self.idp.exchanges
        self.assertEqual(self.callback(third,s3,c3)[0],401)
        self.assertEqual(self.idp.exchanges,count)

    def test_jwks_duplicate_and_redirect_rejected(self):
        original=self.idp.keys
        try:
            self.idp.keys=[self.idp.jwk,self.idp.jwk]
            browser,url=self.start()
            state,code=self.idp.issue(url,"duplicate")
            self.assertEqual(self.callback(browser,state,code)[0],401)
        finally:
            self.idp.keys=original
        provider=OIDCProvider(replace(self.settings,jwks_uri=self.idp.issuer+"/redirect"),
                              self.idp.secret,ssl_context=self.idp.context)
        auth=AuthenticationService(self.core.identity,provider)
        browser,url=auth.begin()
        state,code=self.idp.issue(url,"redirect")
        with self.assertRaises(AuthError):
            auth.complete(browser,state,code)

    def test_session_expiry_logout_restart_and_header_spoof(self):
        cookie,actor=self.login("session-subject")
        self.assertEqual(self.request("GET","/api/me",headers={"X-Actor":actor,
                         "Authorization":"Bearer "+actor})[0],401)
        self.assertEqual(self.request("GET","/api/me",cookie=cookie,
                         headers={"Host":"evil.invalid"})[0],400)
        self.assertEqual(self.request("POST","/auth/logout",cookie=cookie,body={},origin=False)[0],401)
        self.assertEqual(self.get(cookie,"/api/me")["account_id"],actor)
        self.assertEqual(self.request("POST","/auth/logout",cookie=cookie,body={})[0],303)
        self.assertEqual(self.request("GET","/api/me",cookie=cookie)[0],401)
        fresh,_=self.login("session-subject")
        token=fresh.split("=",1)[1]
        new=AuthenticationService(build_core(self.config).identity,self.provider)
        with self.assertRaises(AuthError):
            new.actor(token)
        with patch("montara_core.application.identity.time.monotonic",
                   return_value=time.monotonic()+3601):
            self.assertEqual(self.request("GET","/api/me",cookie=fresh)[0],401)

    def test_http_two_accounts_four_projects_and_owner_scoped_download(self):
        a,aid=self.login("owner-a")
        b,bid=self.login("owner-b")
        self.assertNotEqual(aid,bid)
        results=[]
        for i,(cookie,actor) in enumerate(((a,aid),(b,bid),(a,aid),(b,bid))):
            p=self.post(cookie,"/api/projects",{"name":"same"})["id"]
            folder=self.originals/p
            folder.mkdir()
            (folder/"camera.mp4").write_bytes(("camera-"+str(i)).encode())
            (folder/"master.wav").write_bytes(("master-"+str(i)).encode())
            camera=self.post(cookie,f"/api/projects/{p}/media",
                             {"kind":"video","locator":"camera.mp4"})["representation"]["id"]
            master=self.post(cookie,f"/api/projects/{p}/media",
                             {"kind":"audio","locator":"master.wav"})["representation"]["id"]
            recipe=self.post(cookie,f"/api/projects/{p}/recipes",
                             {"expected_revision":None,"master":master,
                              "shots":[{"representation_id":camera,"start_ms":0,"end_ms":1000}]})["id"]
            job=self.post(cookie,f"/api/projects/{p}/jobs",{"key":"same","recipe_id":recipe})["id"]
            self.assertEqual(self.get(cookie,f"/api/jobs/{job}")["state"],"queued")
            self.get(cookie,f"/api/jobs/{job}/artifact",409)
            ref=self.core.jobs.claim()
            self.assertEqual(ref.job_id,job)
            spec=self.core.media.prepare_execution(ref)
            expected=("fixture-result-"+str(i)).encode()
            self.core.publication.publish(actor,ref,candidate_for(spec,expected))
            status,h,raw=self.request("GET",f"/api/jobs/{job}/artifact",cookie=cookie)
            self.assertEqual((status,raw),(200,expected))
            self.assertEqual(h["Content-Type"],"video/mp4")
            self.assertIn("attachment",h["Content-Disposition"])
            self.assertEqual(h["Cache-Control"],"private, no-store")
            results.append((cookie,p,camera,master,recipe,job,expected))
        for cookie,p,camera,master,recipe,job,expected in results:
            foreign=b if cookie==a else a
            for path in (f"/api/projects/{p}",f"/api/representations/{camera}",
                         f"/api/recipes/{recipe}",f"/api/jobs/{job}",f"/api/jobs/{job}/artifact"):
                self.get(foreign,path,404)
            self.post(foreign,f"/api/projects/{p}/media",{"kind":"video","locator":"camera.mp4"},404)
            self.post(foreign,f"/api/projects/{p}/jobs",{"key":"foreign","recipe_id":recipe},404)
            self.assertEqual(self.request("GET",f"/api/jobs/{job}/artifact")[0],401)
            self.assertEqual(self.request("GET",f"/api/jobs/{job}/artifact",cookie=cookie)[2],expected)
        self.assertEqual(len(self.get(a,"/api/projects")),2)
        self.assertEqual(len(self.get(b,"/api/projects")),2)
        # Same-owner cross-project recipe input remains prohibited.
        _,p,_,master,recipe,_,_=results[0]
        self.post(a,f"/api/projects/{p}/recipes",{"expected_revision":recipe,"master":master,
                  "shots":[{"representation_id":results[2][2],"start_ms":0,"end_ms":1000}]},404)
        self.post(a,"/api/projects",{"name":"spoof","owner_id":bid},400)
        self.assertEqual(self.request("POST","/api/projects",cookie=a,body={"name":"csrf"},
                                      origin=False)[0],401)
        self.assertEqual(self.request("POST","/api/projects",cookie=a,body={"name":"csrf"},
                         headers={"Origin":"https://evil.invalid"},origin=False)[0],401)
        self.assertEqual(self.request("GET","/artifacts/"+results[0][5]+".mp4",cookie=a)[0],404)
        self.assertEqual(self.request("POST","/api/claim",cookie=a,body={})[0],404)
        job=results[0][5]
        receipt=self.core.publication.get_artifact(aid,job)
        receipt.path.write_bytes(b"x"*receipt.size_bytes)
        self.get(a,f"/api/jobs/{job}/artifact",503)

    def test_identity_concurrent_provisioning(self):
        with ThreadPoolExecutor(4) as pool:
            ids=list(pool.map(lambda _:build_core(self.config).identity.resolve_identity(
                self.idp.issuer,"concurrent"),range(8)))
        self.assertEqual(len(set(ids)),1)

    def test_config_secret_and_schema_migration(self):
        self.secret.chmod(0o644)
        with self.assertRaises(AuthError):
            read_client_secret(self.secret)
        self.secret.chmod(0o600)
        link=self.root/"link"
        link.symlink_to(self.secret)
        with self.assertRaises(AuthError):
            read_client_secret(link)
        config=self.root/"config.toml"
        settings=self.settings
        config.write_text(f'[storage]\nsqlite_path="{self.config.sqlite_path}"\n'
                          f'media_root="{self.originals}"\nworkspace_root="{self.workspaces}"\n'
                          f'artifact_root="{self.artifacts}"\n[oidc]\n'
                          + "\n".join(f'{k}="{getattr(settings,k)}"' for k in
                              ("issuer","authorization_endpoint","token_endpoint","jwks_uri",
                               "client_id","client_secret_file","public_origin")))
        self.assertEqual(load_config(config),self.config)
        config.write_text(config.read_text().replace(settings.public_origin,"http://montara.test"))
        with self.assertRaises(ConfigError):
            load_config(config)
        actor=self.core.projects.provision_account().id
        project=self.core.projects.create_project(actor,"prior")
        with sqlite3.connect(self.config.sqlite_path) as db:
            db.execute("DROP TABLE oidc_identities")
            db.execute("PRAGMA user_version=4")
        before=self.config.sqlite_path.read_bytes()
        original=SQLiteDatabase._validate_schema
        def reject(db,ddl):
            original(db,ddl)
            if ddl==_SCHEMA_5:
                raise StorageError("injected")
        with patch.object(SQLiteDatabase,"_validate_schema",staticmethod(reject)):
            with self.assertRaises(StorageError):
                build_core(self.config)
        self.assertEqual(self.config.sqlite_path.read_bytes(),before)
        reopened=build_core(self.config)
        self.assertEqual(reopened.projects.get_project(actor,project.id),project)
        self.assertNotEqual(reopened.identity.resolve_identity(self.idp.issuer,actor),actor)
