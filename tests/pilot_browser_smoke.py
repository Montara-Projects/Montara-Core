"""Installed browser -> OIDC test IdP -> HTTP -> real worker CLI -> MP4 gate.

Chromium and a test-only HTTPS reverse proxy, not Google or deployed TLS.
Uses one canonical real render. No credentials, private paths or IDs in report.
"""
import array
import base64
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time

from playwright.sync_api import sync_playwright, expect
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.jobs import JobState
from montara_core.adapters.sqlite_db import SQLiteDatabase
from test_oidc_http import TestIdP


class TestProxy:
    """Loopback fixture only: preserve Host/Origin/cookies and all Set-Cookie."""
    def __init__(self, cert, key):
        proxy=self
        self.target=None
        class Handler(BaseHTTPRequestHandler):
            def forward(self):
                # Test harness only: exercise Chromium's TLS/download path
                # before spending a real render, never substitute product MP4.
                if self.command=="GET" and self.path in ("/__qa-download-page","/__qa-download"):
                    probe=self.path=="/__qa-download"
                    payload=b"qa-transport-probe" if probe else b'<a href="/__qa-download">Probe</a>'
                    self.send_response(200)
                    self.send_header("Content-Type","application/octet-stream" if probe else "text/html")
                    if probe:
                        self.send_header("Content-Disposition",'attachment; filename="probe.bin"')
                    self.send_header("Content-Length",str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                conn=HTTPConnection("127.0.0.1",proxy.target,timeout=90)
                try:
                    body=self.rfile.read(int(self.headers.get("Content-Length","0"))) if self.command=="POST" else None
                    headers={k:v for k,v in self.headers.items() if k.lower() not in
                             ("connection","proxy-connection","transfer-encoding","accept-encoding")}
                    conn.request(self.command,self.path,body=body,headers=headers)
                    result=conn.getresponse()
                    payload=result.read()
                    self.send_response(result.status)
                    for k,v in result.getheaders():
                        if k.lower() not in ("server","date","connection","transfer-encoding","content-length"):
                            self.send_header(k,v)
                    self.send_header("Content-Length",str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    conn.close()
            do_GET=forward
            do_POST=forward
            def log_message(self,*args): pass
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key)
        self.server.socket=tls.wrap_socket(self.server.socket,server_side=True)
        self.origin="https://localhost:"+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


def main():
    jm,engine,out=map(lambda x:Path(x).resolve(),sys.argv[1:])
    out.mkdir(mode=0o700)
    fixture_dir=out/"idp"; fixture_dir.mkdir()
    idp=TestIdP(fixture_dir)
    idp.browser_enabled=True
    proxy=TestProxy(fixture_dir/"test-ca.pem",fixture_dir/"test-key.pem")
    idp.origin=proxy.origin
    server=None
    try:
        loader=importlib.util.spec_from_file_location("jm_fixture",jm/"tools/core_v2_media_fixture.py")
        media=importlib.util.module_from_spec(loader); loader.loader.exec_module(media)
        bank=out/"bank"; media.generate(bank); before=media.verify_inputs(bank)
        originals,workspaces,artifacts=[out/n for n in ("originals","workspaces","artifacts")]
        for p in (originals,workspaces,artifacts): p.mkdir(mode=0o700)
        secret=out/"client-secret"; secret.write_text(idp.secret); secret.chmod(0o600)
        settings=idp.settings(secret)
        config_path=out/"core.toml"
        config_path.write_text("[http]\nport=0\n[storage]\n"+
            "\n".join(f"{k}={json.dumps(str(v))}" for k,v in {
                "sqlite_path":out/"core.db","media_root":originals,
                "workspace_root":workspaces,"artifact_root":artifacts}.items())+
            "\n[oidc]\n"+"\n".join(f"{k}={json.dumps(str(getattr(settings,k)))}" for k in
                ("issuer","authorization_endpoint","token_endpoint","jwks_uri","client_id",
                 "client_secret_file","public_origin"))+
            '\n[pilot]\nallowed_subjects=["owner-a","owner-b"]\n'+
            f"renderer_root={json.dumps(str(jm))}\nengine_root={json.dumps(str(engine))}\nrender_timeout_sec=180\n")
        config_path.chmod(0o600)
        config=load_config(config_path)
        command=[sys.executable,"-I","-m","montara_core","--config",str(config_path)]
        env={**os.environ,"SSL_CERT_FILE":str(fixture_dir/"test-ca.pem")}
        server=subprocess.Popen(command,cwd=out,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        line=server.stdout.readline()
        match=re.search(r"127\.0\.0\.1:(\d+)",line)
        assert match,"installed HTTP startup failed"
        proxy.target=int(match[1])

        # Playwright context ignore_https_errors does not reliably cover
        # Chromium's separate download network path. Pin only the ephemeral
        # fixture certificate SPKI in this test browser; trust the fixture CA
        # in the Node API client. No global certificate bypass in product.
        cert=fixture_dir/"test-ca.pem"
        pubkey=subprocess.check_output(["openssl","x509","-in",str(cert),"-pubkey","-noout"],timeout=10)
        der=subprocess.check_output(["openssl","pkey","-pubin","-outform","der"],input=pubkey,timeout=10)
        pin=base64.b64encode(hashlib.sha256(der).digest()).decode("ascii")
        os.environ["NODE_EXTRA_CA_CERTS"]=str(cert)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel="chromium",headless=True,
                args=["--ignore-certificate-errors-spki-list="+pin])
            probe_context=browser.new_context(accept_downloads=True)
            probe_page=probe_context.new_page()
            probe_page.goto(proxy.origin+"/__qa-download-page")
            with probe_page.expect_download() as transport:
                probe_page.get_by_role("link",name="Probe").click()
            assert transport.value.failure() is None,"QA HTTPS browser download transport failed"
            probe_file=out/"transport-probe.bin"
            transport.value.save_as(probe_file)
            assert probe_file.read_bytes()==b"qa-transport-probe"
            probe_context.close()
            def login(subject):
                context=browser.new_context(accept_downloads=True)
                context.add_cookies([{"name":"fixture-subject","value":subject,
                    "domain":"127.0.0.1","path":"/authorize","secure":True,"sameSite":"Lax"}])
                page=context.new_page()
                page.goto(proxy.origin+"/")
                expect(page.locator("#login")).to_be_visible()
                page.locator("#login").click()
                if subject=="denied":
                    expect(page.locator("body")).to_contain_text("authentication_required")
                    return context,page,None
                expect(page.locator("#panel")).to_be_visible()
                expect(page.locator("#status")).to_have_text("Sesión activa.")
                account=page.locator("#account").inner_text()
                assert re.fullmatch(r"[0-9a-f-]{36}",account)
                session=next(c for c in context.cookies(proxy.origin) if c["name"]=="__Host-montara-session")
                assert session["secure"] and session["httpOnly"] and session["sameSite"]=="Lax"
                return context,page,account
            denied,_,_=login("denied")
            denied.close()
            ca,a,owner_a=login("owner-a")
            cb,b,owner_b=login("owner-b")
            assert owner_a!=owner_b
            with SQLiteDatabase(config.sqlite_path)._connection() as db:
                assert db.execute("SELECT count(*) FROM oidc_identities").fetchone()==(2,)
            a.locator("#name").fill("Fixture A")
            a.locator("#create").click()
            expect(a.locator("#project-id")).to_have_text(re.compile(r"^[0-9a-f-]{36}$"))
            expect(a.locator("#register")).to_be_enabled()
            project=a.locator("#project-id").inner_text()
            folder=originals/project; folder.mkdir()
            for name in ("camera-a.mp4","camera-b.mp4","master.wav"):
                shutil.copyfile(bank/name,folder/name)
            original_hashes={p.name:media.sha256(p) for p in folder.iterdir()}
            a.locator("#register").click()
            expect(a.locator("#media li")).to_have_count(3)
            expect(a.locator("#recipe")).to_be_enabled()
            a.locator("#recipe").click()
            expect(a.locator("#recipe-state")).to_contain_text("Revisión 1")
            expect(a.locator("#submit")).to_be_enabled()
            a.locator("#submit").click()
            expect(a.locator("#jobs")).to_contain_text("queued")
            core=build_core(config)
            snapshot=core.media.pilot_summary(owner_a,project)
            job=snapshot["jobs"][0]["id"]
            assert core.jobs.get_job(owner_a,job).state==JobState.QUEUED
            assert b.request.get(proxy.origin+"/api/projects/"+project+"/pilot").status==404
            assert b.request.get(proxy.origin+"/api/jobs/"+job+"/artifact").status==404
            assert a.request.get(proxy.origin+"/api/jobs/"+job+"/artifact").status==409
            assert a.request.post(proxy.origin+"/api/claim",data={},headers={"Origin":proxy.origin}).status==404

            # Exactly one real render from installed worker entrypoint; no actor
            # or job ID passed on its CLI, claim derives owner in SQLite.
            started=time.monotonic()
            worker=subprocess.run(command+["--worker-once"],cwd=out,env=env,
                                  capture_output=True,text=True,timeout=420)
            assert worker.returncode==0, "worker failed: "+str(worker.returncode)
            report=json.loads(worker.stdout)
            assert report["worker"]=="published" and report["job_id"]==job
            expect(a.locator("#jobs")).to_contain_text("succeeded",timeout=15000)
            # Diagnostic HTTP response must already be authorized and intact;
            # the actual browser download below remains mandatory.
            served=a.request.get(proxy.origin+"/api/jobs/"+job+"/artifact")
            assert served.status==200 and served.headers["content-type"]=="video/mp4"
            assert "attachment" in served.headers["content-disposition"]
            assert len(served.body())==int(served.headers["content-length"])
            with a.expect_download() as download:
                a.get_by_role("link",name="Descargar MP4").click()
            downloaded=out/"downloaded.mp4"
            assert download.value.failure() is None,"real browser MP4 download failed"
            download.value.save_as(downloaded)
            assert downloaded.read_bytes()==served.body()
            receipt=build_core(config).publication.get_artifact(owner_a,job)
            assert media.sha256(downloaded)==receipt.sha256==media.sha256(receipt.path)
            assert downloaded.stat().st_size==receipt.size_bytes
            assert build_core(config).jobs.get_job(owner_a,job).state==JobState.SUCCEEDED
            assert media.verify_inputs(bank)==before
            assert {p.name:media.sha256(p) for p in folder.iterdir()}==original_hashes
            assert b.request.get(proxy.origin+"/api/jobs/"+job).status==404
            assert b.request.get(proxy.origin+"/api/jobs/"+job+"/artifact").status==404
            assert b.request.get(proxy.origin+"/api/projects").json()==[]
            fresh=browser.new_context()
            assert fresh.request.get(proxy.origin+"/api/jobs/"+job+"/artifact").status==401
            fresh.close()
            # Browser reload and owner project selection recover durable job.
            a.reload(); expect(a.locator("#status")).to_have_text("Sesión activa.")
            a.locator("#projects").select_option(project)
            expect(a.locator("#jobs")).to_contain_text("succeeded")
            a.locator("#logout").click()
            expect(a.locator("#login")).to_be_visible()
            assert a.request.get(proxy.origin+"/api/jobs/"+job+"/artifact").status==401
            a.locator("#login").click(); expect(a.locator("#status")).to_have_text("Sesión activa.")
            assert a.locator("#account").inner_text()==owner_a
            a.locator("#projects").select_option(project)
            expect(a.locator("#jobs")).to_contain_text("succeeded")
            idle=subprocess.run(command+["--worker-once"],cwd=out,env=env,capture_output=True,text=True,timeout=30)
            assert idle.returncode==0 and json.loads(idle.stdout)["worker"]=="idle"
            ca.close(); cb.close(); browser.close()

        probe=media.probe(downloaded)
        video=next(s for s in probe["streams"] if s["codec_type"]=="video")
        audio=next(s for s in probe["streams"] if s["codec_type"]=="audio")
        assert (video["width"],video["height"],video["avg_frame_rate"],video["codec_name"],audio["codec_name"])==(1920,1080,"30/1","h264","aac")
        assert abs(float(probe["format"]["duration"])-6)<.15
        media.decode(downloaded)
        frames=[]
        for at,color in [(1.5,"red"),(2.25,"blue"),(2.8,"blue"),(3.5,"yellow"),(4.5,"green")]:
            actual,rgb=media.frame_color(downloaded,at)
            assert actual==color,(at,actual,color)
            frames.append({"at":at,"color":actual})
        tones=[]
        for at in (1,3,5):
            raw=media.execute(["ffmpeg","-v","error","-ss",str(at),"-i",str(downloaded),
                               "-t","0.25","-vn","-ac","1","-ar",str(media.RATE),"-f","s16le","-"])
            samples=array.array("h",raw)
            if sys.byteorder!="little": samples.byteswap()
            values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}
            assert values[997]>=50 and values[997]>=10*max(values[223],values[443])
            tones.append({"at":at,"master_dominant":True})
        evidence={"status":"PASS","scope":"Chromium HTTPS fixture OIDC -> installed Core HTTP -> installed worker CLI -> real Engine -> publication -> browser MP4 download",
                  "browser":"Chromium","browser_transport_preflight":"PASS","admission":"PASS","owner_isolation":"PASS","logout_relogin":"PASS",
                  "worker":"PASS","worker_idle_no_duplicate":"PASS","publication":"PASS",
                  "published_reopen":"PASS","browser_download_digest":"PASS","full_decode":"PASS",
                  "duration_sec":float(probe["format"]["duration"]),"resolution":"1920x1080","fps":30,
                  "video_codec":"h264","audio_codec":"aac","sha256":receipt.sha256,"size_bytes":receipt.size_bytes,
                  "frames":frames,"tones":tones,"originals_unchanged":True,"google_real":"NOT_RUN",
                  "production_proxy":"NOT_RUN"}
        (out/"quality-report.json").write_text(json.dumps(evidence,indent=2))
        print(json.dumps(evidence,indent=2))
    finally:
        if server is not None:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait()
        proxy.close(); idp.close()


if __name__=="__main__":
    main()
