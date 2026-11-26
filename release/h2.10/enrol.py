"""One operator-armed Google OIDC ceremony. No Core DB, account or session."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

COOKIE = "__Host-montara-login"
HTML = """<!doctype html><html lang="es"><meta charset="utf-8"><title>Montara - DEVELOPMENT</title><h1>Montara - enrolamiento privado</h1><p>Selecciona exclusivamente la cuenta Google confirmada con el operador.</p><form><label>Codigo de enrolamiento <input id="code" type="password" autocomplete="off" required></label><button>Continuar con Google</button></form><p id="message"></p><script src="/pilot.js" defer></script></html>"""
JS = """document.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();const c=document.querySelector('#code');const code=c.value;c.value='';try{const r=await fetch('/api/enrol',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});if(!r.ok)throw Error();location.assign((await r.json()).url)}catch{document.querySelector('#message').textContent='No autorizado. Consulta al operador.'}});"""
class Ceremony:
    def __init__(self, provider, issuer, digest, result, clock=time.monotonic):
        self.provider, self.issuer, self.digest, self.result, self.clock = provider, issuer, digest, result, clock
        self.deadline = clock()+600
        self.pending = None
        self.used = False
        self.attempts = 0
    def begin(self, code):
        if self.used or self.clock() >= self.deadline or self.attempts >= 8:
            raise ValueError("closed")
        self.attempts += 1
        if not isinstance(code,str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",code):
            raise ValueError("denied")
        if not secrets.compare_digest(hashlib.sha256(code.encode()).hexdigest(),self.digest):
            raise ValueError("denied")
        self.used = True
        browser,state,nonce,verifier = (secrets.token_urlsafe(32) for _ in range(4))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.pending = (hashlib.sha256(browser.encode()).hexdigest(),state,nonce,verifier,min(self.deadline,self.clock()+300))
        return browser,self.provider.authorization_url(state,nonce,challenge)
    def complete(self, browser, state, code):
        pending,self.pending = self.pending,None
        if (pending is None or self.clock()>=pending[4] or not isinstance(browser,str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}",browser)
            or not secrets.compare_digest(hashlib.sha256(browser.encode()).hexdigest(),pending[0])
            or not isinstance(state,str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}",state)
            or not secrets.compare_digest(state,pending[1]) or not isinstance(code,str)
            or not 1<=len(code)<=4096):
            raise ValueError("invalid transaction")
        issuer,sub = self.provider.exchange(code,pending[3],pending[2])
        if issuer!=self.issuer or not isinstance(sub,str) or not re.fullmatch(r"[\x21-\x7e]{1,255}",sub):
            raise ValueError("invalid identity")
        # Exclusive private output. No token, email or subject in response.
        fd=os.open(self.result,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,"w",encoding="ascii") as f:
            f.write(sub+"\n")
            f.flush()
            os.fsync(f.fileno())

def handler(ceremony, origin):
    host=urlsplit(origin).netloc
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()
        def log_message(self,*args): pass
        def log_error(self,*args): pass
        def send_error(self,code,message=None,explain=None): self.reply(code,b"Unavailable")
        def reply(self,status,body=b"",kind="text/plain; charset=utf-8",cookie=None):
            self.send_response(status)
            self.send_header("Content-Type",kind)
            self.send_header("Cache-Control","private, no-store")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy","default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header("Content-Length",str(len(body)))
            if cookie: self.send_header("Set-Cookie",cookie)
            self.end_headers()
            self.wfile.write(body)
        def valid(self):
            return self.headers.get_all("Host")==[host] and len(self.path)<=8192
        def do_GET(self):
            if not self.valid(): return self.reply(403,b"Unavailable")
            if self.path=="/": return self.reply(200,HTML.encode(),"text/html; charset=utf-8")
            if self.path=="/pilot.js": return self.reply(200,JS.encode(),"text/javascript; charset=utf-8")
            if self.path=="/health": return self.reply(200,b"ENROLMENT_READY")
            if self.path.split("?",1)[0]!="/auth/callback": return self.reply(404,b"Unavailable")
            clear=COOKIE+"=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0"
            try:
                q=parse_qs(urlsplit(self.path).query,strict_parsing=True,max_num_fields=8)
                if not {"state","code"}<=q.keys() or "error" in q or any(len(v)!=1 for v in q.values()):
                    raise ValueError("callback")
                # Reject duplicate browser cookies instead of choosing one.
                values=[part.strip().split("=",1)[1] for line in self.headers.get_all("Cookie",[]) for part in line.split(";") if part.strip().startswith(COOKIE+"=")]
                if len(values)!=1: raise ValueError("cookie")
                ceremony.complete(values[0],q["state"][0],q["code"][0])
            except Exception:
                ceremony.pending=None
                return self.reply(403,b"Enrolamiento rechazado. Consulta al operador.",cookie=clear)
            return self.reply(200,b"Enrolamiento recibido. Confirma la cuenta seleccionada con el operador. Cierra esta pestana.",cookie=clear)
        def do_POST(self):
            if not self.valid() or self.path!="/api/enrol" or self.headers.get_all("Origin")!=[origin] or self.headers.get_all("Content-Type")!=["application/json"] or self.headers.get("Transfer-Encoding"):
                return self.reply(403,b"Unavailable")
            try:
                lengths=self.headers.get_all("Content-Length",[])
                if len(lengths)!=1 or not re.fullmatch(r"[0-9]{1,3}",lengths[0]) or not 1<=int(lengths[0])<=256: raise ValueError("length")
                raw=self.rfile.read(int(lengths[0]))
                if len(raw)!=int(lengths[0]): raise ValueError("body")
                d=json.loads(raw)
                if not isinstance(d,dict) or set(d)!={"code"}: raise ValueError("body")
                browser,url=ceremony.begin(d["code"])
                cookie=COOKIE+"="+browser+"; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=300"
                return self.reply(200,json.dumps({"url":url}).encode(),"application/json",cookie)
            except Exception:
                return self.reply(403,b"Unavailable")
    return Handler

def main():
    # The installed, approved wheel performs provider exchange and cryptographic verification.
    from montara_core.adapters.oidc import OIDCProvider, read_client_secret
    from montara_core.application.identity import OIDCSettings
    cfg=Path("/etc/montara-pilot-enrol")
    d=json.loads((cfg/"oidc.json").read_text())
    d["client_secret_file"]=cfg/"client-secret"
    settings=OIDCSettings(**d)
    digest=(cfg/"arm.sha256").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{64}",digest): raise ValueError("not armed")
    provider=OIDCProvider(settings,read_client_secret(settings.client_secret_file))
    result=Path("/run/montara-pilot-enrol/result")
    if result.exists() or result.is_symlink(): raise ValueError("stale result")
    c=Ceremony(provider,settings.issuer,digest,result)
    with HTTPServer(("127.0.0.1",8080),handler(c,settings.public_origin)) as server:
        server.serve_forever()
if __name__=="__main__":
    main()
