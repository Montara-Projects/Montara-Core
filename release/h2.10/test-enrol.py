"""Focused tests for the transient enrolment only, no wheel/Google/Cloudflare."""
import base64
import hashlib
import http.client
import importlib.util
import json
import secrets
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlencode
from http.server import HTTPServer
spec=importlib.util.spec_from_file_location("enrol",Path(__file__).with_name("enrol.py"))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
ORIGIN="https://montara.fixture.workers.dev"; HOST="montara.fixture.workers.dev"
class Provider:
    def __init__(self):
        self.calls=0; self.sub="synthetic-only-subject"; self.issuer="https://accounts.google.com"; self.reject=False
    def authorization_url(self,state,nonce,challenge):
        self.state,self.nonce,self.challenge=state,nonce,challenge
        return "https://accounts.google.com/fixture"
    def exchange(self,code,verifier,nonce):
        self.calls+=1
        assert nonce==self.nonce
        assert self.challenge==base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if self.reject: raise ValueError("synthetic confidential error")
        assert code=="fixture"
        return self.issuer,self.sub
def rejects(fn):
    try: fn()
    except (ValueError,OSError): return
    raise AssertionError("accepted invalid input")
def new(d,clock=lambda:1):
    code=secrets.token_urlsafe(32);p=Provider()
    c=e.Ceremony(p,p.issuer,hashlib.sha256(code.encode()).hexdigest(),d/"result",clock)
    return c,p,code
with tempfile.TemporaryDirectory() as name:
    d=Path(name)
    c,p,code=new(d)
    rejects(lambda:c.begin("wrong"))
    browser,url=c.begin(code);assert url.startswith("https://accounts.google.com/")
    rejects(lambda:c.begin(code))
    c.complete(browser,p.state,"fixture")
    assert (d/"result").read_text()==p.sub+"\n"
    assert (d/"result").stat().st_mode&0o777==0o600 and p.calls==1
    rejects(lambda:c.complete(browser,p.state,"fixture"));assert p.calls==1
    (d/"result").unlink();print("ONE_USE_PKCE_PRIVATE_OUTPUT=PASS")
    for which in ("state","browser","issuer","subject","provider","expiry","symlink","attempts"):
        now=[1];c,p,code=new(d,lambda:now[0])
        if which=="attempts":
            for i in range(8): rejects(lambda:c.begin("wrong"))
            rejects(lambda:c.begin(code));assert not p.calls
        else:
            browser,url=c.begin(code);state=p.state
            if which=="state": state=secrets.token_urlsafe(32)
            elif which=="browser": browser=secrets.token_urlsafe(32)
            elif which=="issuer":p.issuer="https://wrong.invalid"
            elif which=="subject":p.sub="invalid\nsubject"
            elif which=="provider":p.reject=True
            elif which=="expiry":now[0]=302
            elif which=="symlink":(d/"result").symlink_to(d/"target")
            rejects(lambda:c.complete(browser,state,"fixture"))
            if which in ("state","browser","expiry"):assert p.calls==0
            if which=="symlink":
                assert not (d/"target").exists();(d/"result").unlink()
            else: assert not (d/"result").exists()
            rejects(lambda:c.complete(browser,p.state,"fixture"))
        print(which.upper()+"_REJECTED=PASS")
    c,p,code=new(d,clock=__import__("time").monotonic)
    with HTTPServer(("127.0.0.1",0),e.handler(c,ORIGIN)) as s:
        thread=threading.Thread(target=s.serve_forever,daemon=True);thread.start()
        def req(method,path,headers=None,body=None):
            conn=http.client.HTTPConnection("127.0.0.1",s.server_port,timeout=5)
            try:
                conn.request(method,path,body=body,headers={"Host":HOST,**(headers or {})})
                r=conn.getresponse();return r.status,r.headers,r.read()
            finally:conn.close()
        try:
            assert req("GET","/",{"Host":"other.invalid"})[0]==403
            assert req("GET","/api/me")[0]==404
            status,h,body=req("GET","/");assert status==200 and b"enrolamiento privado" in body and h["Cache-Control"]=="private, no-store"
            assert req("GET","/pilot.js")[0]==200
            payload=json.dumps({"code":code})
            assert req("POST","/api/enrol",{"Origin":"https://other.invalid","Content-Type":"application/json"},payload)[0]==403
            assert req("POST","/api/enrol",{"Origin":ORIGIN,"Content-Type":"application/json"},"x"*257)[0]==403
            status,h,body=req("POST","/api/enrol",{"Origin":ORIGIN,"Content-Type":"application/json"},payload)
            assert status==200 and json.loads(body)["url"].startswith("https://accounts.google.com/")
            cookie=h["Set-Cookie"];assert "Secure; HttpOnly; SameSite=Lax" in cookie and cookie.startswith(e.COOKIE+"=")
            cookie=cookie.split(";",1)[0]
            query=urlencode({"state":p.state,"code":"fixture"})
            status,h,body=req("GET","/auth/callback?"+query,{"Cookie":cookie})
            assert status==200 and b"synthetic-only-subject" not in body and "Max-Age=0" in h["Set-Cookie"] and p.calls==1
            assert req("GET","/auth/callback?"+query,{"Cookie":cookie})[0]==403 and p.calls==1
            assert (d/"result").read_text()==p.sub+"\n"
            print("HTTP_HOST_ORIGIN_BODY_COOKIE_CALLBACK_REPLAY=PASS")
        finally:s.shutdown();thread.join(timeout=5)
print("ENROLMENT_MICROGATE=PASS")
