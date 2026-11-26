#!/bin/bash
set -euo pipefail
umask 007
export LC_ALL=C.UTF-8 PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export UV_NO_CONFIG=1 UV_CACHE_DIR="$HOME/uv-cache" UV_PYTHON_INSTALL_DIR="$HOME/python" UV_PYTHON_BIN_DIR="$HOME/bin"
unset PYTHONPATH PYTHONHOME PIP_EXTRA_INDEX_URL PIP_TRUSTED_HOST
test "$(id -un)" = montara-test
cd "$HOME"
OUT="$MONTARA_OUTPUT/h2-30-pilot-reference"
if [ -d "$OUT" ] && [ ! -L "$OUT" ] && [ -O "$OUT" ]; then rmdir "$OUT"; fi
test ! -e "$OUT"
mkdir "$OUT"
# Evidence files shared with the authorized connector; scratch stays private.
export OUT
. /etc/os-release
test "$ID" = ubuntu && test "$VERSION_ID" = 24.04
test "$(uname -m)" = x86_64
echo "TARGET=$PRETTY_NAME x86_64"
export CORE=4a7fb469d5a750e1384685695cabb24523ed78a9 PREV=36b010c172370a9c59762cf355e782adc00c3de0 JM=1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d ENGINE=9af275b18535005e36bd289a68ceb4a1d2f6c506
cat > "$HOME/release.py" <<'PY'
import os,sys,subprocess as sp,hashlib,json,shutil,sqlite3,tarfile,zipfile,time,resource,threading,re,stat
from pathlib import Path
from contextlib import contextmanager
home=Path.home()
out=Path(os.environ["OUT"])
core,jm,engine,prev=(os.environ[k] for k in ("CORE","JM","ENGINE","PREV"))
metrics=[]
cg=Path("/sys/fs/cgroup")/next(l.split(":",2)[-1].lstrip("/") for l in Path("/proc/self/cgroup").read_text().splitlines() if l.startswith("0:"))
def cnum(n):
    try: return int((cg/n).read_text().strip())
    except (OSError,ValueError): return None
def cpu():
    a=resource.getrusage(resource.RUSAGE_SELF);b=resource.getrusage(resource.RUSAGE_CHILDREN)
    return (a.ru_utime+b.ru_utime,a.ru_stime+b.ru_stime)
def size(p):
    return sum(x.stat().st_size for x in p.rglob("*") if x.is_file() and not x.is_symlink()) if p.exists() else 0
def sha(p):
    with open(p,"rb") as f: return hashlib.file_digest(f,"sha256").hexdigest()
def save(name,obj):
    p=out/name;p.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n");pass
def run(*a,cwd=None,env=None,capture=False):
    p=sp.run([str(x) for x in a],cwd=cwd,env=env,text=True,stdout=sp.PIPE if capture else None,stderr=sp.STDOUT if capture else None)
    if p.returncode:
        if capture: print(p.stdout[-6000:],flush=True)
        raise RuntimeError("command failed: "+str(a[0])+" rc="+str(p.returncode))
    return p.stdout
@contextmanager
def phase(name,measure_path=None):
    print("PHASE="+name,flush=True)
    t=time.perf_counter();u,s=cpu();disk=shutil.disk_usage(home).used;initial=size(measure_path) if measure_path else None
    stop=threading.Event();peak=[cnum("memory.current")]
    def sample():
        while not stop.wait(.2):
            n=cnum("memory.current")
            if n is not None: peak[0]=max(peak[0] or 0,n)
    th=threading.Thread(target=sample,daemon=True);th.start()
    ok=False
    try:
        yield
        ok=True
    finally:
        stop.set();th.join()
        n=cnum("memory.current")
        if n is not None: peak[0]=max(peak[0] or 0,n)
        u2,s2=cpu()
        rec=dict(phase=name,ok=ok,wall_s=round(time.perf_counter()-t,3),cpu_user_s=round(u2-u,3),cpu_system_s=round(s2-s,3),cgroup_sample_peak_bytes=peak[0],child_maxrss_kib_cumulative=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,filesystem_used_delta_bytes=shutil.disk_usage(home).used-disk,measured_tree_before_bytes=initial,measured_tree_after_bytes=size(measure_path) if measure_path else None)
        metrics.append(rec);save("metrics-install.json",metrics);print("PHASE_RESULT="+json.dumps(rec),flush=True)
def clone(name,url,rev):
    p=home/name
    p.mkdir()
    run("git","init","--quiet",p)
    run("git","remote","add","origin",url,cwd=p)
    run("git","-c","credential.helper=","fetch","--quiet","--depth=1","origin",rev,cwd=p)
    run("git","checkout","--quiet","--detach",rev,cwd=p)
    assert run("git","rev-parse","HEAD",cwd=p,capture=True).strip()==rev
    assert not run("git","status","--porcelain",cwd=p,capture=True).strip()
    return p
def pip(exe,*args,**kwargs): return run(exe,"-m","pip",*args,**kwargs)
def lock_wheels(d):
    return [{"filename":p.name,"sha256":sha(p),"size":p.stat().st_size} for p in sorted(d.glob("*.whl"))]
print("CPU_AFFINITY="+str(len(os.sched_getaffinity(0))),"DISK_FREE_GIB="+str(round(shutil.disk_usage(home).free/2**30,2)),flush=True)
assert shutil.disk_usage(home).free>=15*2**30
with phase("sources"):
    repo=clone("core-src","https://github.com/Montara-Projects/Montara-Core.git",core)
    run("git","-c","credential.helper=","fetch","--quiet","--depth=1","origin",prev,cwd=repo)
    renderer=clone("renderer-src","https://github.com/Montara-Projects/Montara.git",jm)
    eng=clone("engine-src","https://github.com/amehcgracia-byte/zucker_videos.git",engine)
prior=repo/"release/manifests"
oldpy=json.loads((prior/"h2.7-python-lock.json").read_text())
oldff=json.loads((prior/"h2.6-ffmpeg-lock.json").read_text())
oldmanifest=json.loads((prior/"h2.7.json").read_text())
with phase("python_bootstrap"):
    boot=home/"uv-bootstrap";run(sys.executable,"-m","venv",boot)
    uvw=home/"uv-wheels";uvw.mkdir();pip(boot/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",uvw,"uv==0.9.18")
    assert lock_wheels(uvw)==oldpy["uv"],"uv wheel drift"
    pip(boot/"bin/python","install","--quiet","--no-index","--no-deps",*uvw.glob("*.whl"))
    uv=boot/"bin/uv";run(uv,"python","install","3.12.12")
    py=Path(run(uv,"python","find","--managed-python","3.12.12",capture=True).strip())
    assert run(py,"-c","import sys;print(sys.version.split()[0])",capture=True).strip()=="3.12.12"
with phase("ffmpeg_local_closure"):
    # Use APT's signed-index candidate closure, but only download and extract.
    # No apt install/update, dpkg database change or maintainer scripts.
    packages=oldff["packages"]
    assert len(packages)==148
    debs=home/"debs";debs.mkdir()
    run("apt-get","-qq","download",*packages,cwd=debs)
    ffroot=home/"ffmpeg-root";ffroot.mkdir()
    deb_lock=[]
    for d in sorted(debs.glob("*.deb")):
        ident=run("dpkg-deb","-f",d,"Package","Version","Architecture",capture=True).strip()
        deb_lock.append({"filename":d.name,"metadata":ident,"sha256":sha(d),"size":d.stat().st_size})
        run("dpkg-deb","-x",d,ffroot)
    assert len(deb_lock)==len(packages),(len(deb_lock),len(packages))
    assert deb_lock==oldff["debs"],"FFmpeg .deb drift"
    bindir=home/"media-tools";bindir.mkdir()
    lib=ffroot/"usr/lib/x86_64-linux-gnu"
    libdirs=[lib,ffroot/"lib/x86_64-linux-gnu"]
    libdirs+=sorted({p.parent for p in ffroot.rglob("*.so*") if p.is_file()}-set(libdirs))
    libpath=":".join(map(str,libdirs))
    for name in ("ffmpeg","ffprobe"):
        real=ffroot/"usr/bin"/name
        assert real.is_file()
        w=bindir/name
        w.write_text('#!/bin/sh\nexport LD_LIBRARY_PATH="'+libpath+'"\nexec "'+str(real)+'" "$@"\n')
        w.chmod(0o700)
    os.environ["PATH"]=str(bindir)+":"+os.environ["PATH"]
    ffver=run("ffmpeg","-version",capture=True).splitlines()[0]
    fpver=run("ffprobe","-version",capture=True).splitlines()[0]
    assert "6.1.1" in ffver and "6.1.1" in fpver
    enc=run("ffmpeg","-hide_banner","-encoders",capture=True)
    assert "libx264" in enc and " aac " in enc
    run("ffmpeg","-v","error","-f","lavfi","-i","color=black:s=64x64:r=30","-t","0.1","-c:v","libx264","-f","null","-")
    # Record dynamic closure actually resolved, including pre-existing host libraries.
    host_libs={}
    for name in ("ffmpeg","ffprobe"):
        lddenv=dict(os.environ,LD_LIBRARY_PATH=libpath)
        lines=run("ldd",ffroot/"usr/bin"/name,env=lddenv,capture=True).splitlines()
        assert not any("not found" in l for l in lines)
        for line in lines:
            match=re.search(r"(?:=> )?(/[^ ]+)",line)
            if match:
                p=Path(match.group(1))
                if p.is_file() and not p.is_relative_to(ffroot):
                    host_libs[str(p)]=sha(p)
    assert host_libs==oldff["host_library_sha256"],"FFmpeg host library drift"
    assert {n:sha(ffroot/"usr/bin"/n) for n in ("ffmpeg","ffprobe")}==oldff["binary_sha256"]
    print("FFMPEG_V4_LOCK_REPLAY=PASS",flush=True)
    save("ffmpeg-lock.json",{"source":"Ubuntu 24.04 signed APT indices (download only)","requested":"ffmpeg=7:6.1.1-3ubuntu5","packages":packages,"debs":deb_lock,"host_library_sha256":host_libs,"ffmpeg":ffver,"ffprobe":fpver,"binary_sha256":{n:sha(ffroot/"usr/bin"/n) for n in ("ffmpeg","ffprobe")},"redistribute":False})
    print("FFMPEG="+ffver+" packages="+str(len(deb_lock)),flush=True)
with phase("python_wheel_closure"):
    wheelhouse=home/"wheelhouse";wheelhouse.mkdir()
    oidc_req=["PyJWT==2.15.1","cryptography==50.0.2","cffi==2.1.1","pycparser==3.12"]
    req=oldpy["runtime"]
    buildreq=oldpy["build"]
    downloadenv=home/"download-env";run(py,"-m","venv",downloadenv)
    pip(downloadenv/"bin/python","download","--quiet","--only-binary=:all:","--no-deps","-d",wheelhouse,*req,*buildreq)
    wlocks=lock_wheels(wheelhouse)
    assert len(wlocks)==len(req)+len(buildreq)
    assert wlocks==oldpy["wheel_files"],"historical Python wheel drift"
    print("PYTHON_V5_LOCK_REPLAY=PASS",flush=True)
    def locked(requirements,dest):
        lines=[]
        for r in requirements:
            name,version=r.split("==")
            name=name.lower().replace("-","_")
            matches=[p for p in wheelhouse.glob("*.whl") if p.name.lower().split("-")[0]==name and p.name.split("-")[1]==version]
            assert len(matches)==1,r
            lines.append(r+" --hash=sha256:"+sha(matches[0]))
        dest.write_text("\n".join(lines)+"\n")
    runtime_lock=home/"runtime.lock";build_lock=home/"build.lock"
    locked(req,runtime_lock);locked(buildreq,build_lock)
    assert runtime_lock.read_text()==oldpy["runtime_lock"] and build_lock.read_text()==oldpy["build_lock"]
    oidclock=home/"oidc.lock";locked(oidc_req,oidclock)
    assert oidclock.read_text()==oldpy["oidc_lock"]
    shutil.copyfile(prior/"h2.7-python-lock.json",out/"python-lock.json")
    buildenv=home/"build-env";run(py,"-m","venv",buildenv)
    build=buildenv/"bin/python"
    pip(build,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",build_lock)
    pip(build,"check")
def make(label,rev,schema):
    src=home/("source-"+label);src.mkdir()
    assert run("git","rev-parse",rev+"^{commit}",cwd=repo,capture=True).strip()==rev
    epoch=run("git","show","-s","--format=%ct",rev,cwd=repo,capture=True).strip()
    tree=run("git","rev-parse",rev+"^{tree}",cwd=repo,capture=True).strip()
    tar=home/(label+".tar");run("git","archive","--format=tar","-o",tar,rev,cwd=repo)
    with tarfile.open(tar) as t:t.extractall(src,filter="data")
    tar.unlink();d=home/("wheel-"+label);d.mkdir()
    pip(build,"wheel","--quiet","--no-build-isolation","--no-index","--no-deps","-w",d,src,env=dict(os.environ,SOURCE_DATE_EPOCH=epoch,PYTHONHASHSEED="0"))
    w,=d.glob("*.whl")
    with zipfile.ZipFile(w) as z:
        files={n:hashlib.sha256(z.read(n)).hexdigest() for n in sorted(z.namelist()) if not n.endswith("/")}
    return w,{"source_commit":rev,"source_tree":tree,"schema":schema,"wheel":w.name,"sha256":sha(w),"files":files,"source_date_epoch":int(epoch)}
with phase("build_core"):
    wa,ma=make("pilot-a",core,5);wb,mb=make("pilot-b",core,5);wh,mh=make("historical-v5",prev,5)
    assert ma["files"]==mb["files"] and ma["sha256"]==mb["sha256"]
    assert mh["sha256"]==oldmanifest["core"]["sha256"]
    assert all("montara_core/api/pilot."+e in ma["files"] for e in ("html","css","js"))
    save("build-manifest.json",{"core":ma,"historical_v5":mh,"double_build_equal":True})
    shutil.copyfile(wa,out/wa.name)
    print("DOUBLE_BUILD_UI_RESOURCES=PASS",flush=True)
inp=Path(os.environ["MONTARA_INPUT"])
archived=inp/"h2-30-historical-montara_core-0.1.0-py3-none-any.whl"
mp4=inp/"h2-30-historical-published-v5-fixture.mp4"
assert sha(archived)==mh["sha256"] and sha(mp4)=="e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f"
outside=home/"outside";outside.mkdir()
def install_slot(label,w,m):
    slot=home/label;slot.mkdir();shutil.copyfile(w,slot/m["wheel"])
    run(py,"-m","venv",slot/"venv");exe=slot/"venv/bin/python"
    pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,"--require-hashes","-r",runtime_lock)
    pip(exe,"install","--quiet","--no-index","--find-links",wheelhouse,str(slot/m["wheel"])+"[oidc]")
    pip(exe,"check")
    run(exe,"-I","-c","import pathlib,sys,montara_core; assert pathlib.Path(montara_core.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()); print('INSTALLED_PACKAGE=PASS')",cwd=outside)
    return exe
with phase("offline_install"):
    oldexe=install_slot("slot-historical",archived,mh);exe=install_slot("slot-pilot",wa,ma)
    if os.environ.get("H2_REUSE_SUITE")=="1":
        print("SUITE_REUSED=100/100 earlier attempt exact source/locks; not rerun",flush=True)
    else:
        run(exe,"-I","-m","unittest","discover","-s",repo/"tests","-v",cwd=outside)
    run(exe,"-I","-c","from importlib.resources import files; import montara_core.api; assert all(files(montara_core.api).joinpath('pilot.'+e).is_file() for e in ('html','css','js')); print('UI_PACKAGE_DATA=PASS')",cwd=outside)
with phase("qa_browser_setup"):
    qa_wheels=home/"qa-wheels";qa_wheels.mkdir()
    pip(downloadenv/"bin/python","download","--quiet","--only-binary=:all:","-d",qa_wheels,"playwright==1.56.0")
    qa=home/"qa-only";qa.mkdir()
    pip(downloadenv/"bin/python","install","--quiet","--no-index","--no-deps","--target",qa,*qa_wheels.glob("*.whl"))
    save("qa-wheel-lock.json",lock_wheels(qa_wheels))
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]=str(home/"browsers")
    run(exe,"-I","-c","import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('playwright',run_name='__main__')",qa,"install","chromium","--no-shell")
    # Browser libraries local only, resolved through signed APT indices, no install.
    names=["libnss3","libnspr4","libatk1.0-0t64","libatk-bridge2.0-0t64","libatspi2.0-0t64","libcups2t64","libxcomposite1","libxdamage1","libxfixes3","libxrandr2","libgbm1","libasound2t64","libpango-1.0-0","libcairo2","libxkbcommon0","libdbus-1-3","libx11-xcb1","libxcb1","libxext6","libdrm2","libx11-6","libglib2.0-0t64"]
    sim=run("apt-get","-s","--no-install-recommends","install",*names,capture=True)
    qpackages=[]
    for l in sim.splitlines():
        m=re.match(r"Inst (\S+)(?: \[[^\]]+\])? \((\S+)",l)
        if m and m.group(1)+"="+m.group(2) not in packages:qpackages.append(m.group(1)+"="+m.group(2))
    qdebs=home/"qa-debs";qdebs.mkdir();qroot=home/"qa-libs";qroot.mkdir()
    if qpackages:run("apt-get","-qq","download",*qpackages,cwd=qdebs)
    qlock=[]
    for d in sorted(qdebs.glob("*.deb")):
        qlock.append({"filename":d.name,"sha256":sha(d),"size":d.stat().st_size})
        run("dpkg-deb","-x",d,qroot)
    dirs=sorted({p.parent for p in qroot.rglob("*.so*") if p.is_file()})
    os.environ["QA_LIBPATH"]=":".join(map(str,dirs))+":"+libpath
    save("qa-browser-lock.json",{"playwright":"1.56.0","packages":qpackages,"debs":qlock,"qa_only":True})
    chromes=list((home/"browsers").glob("chromium-*/chrome-linux/chrome"))
    assert len(chromes)==1
    browserenv=dict(os.environ,LD_LIBRARY_PATH=os.environ["QA_LIBPATH"])
    deps=run("ldd",chromes[0],env=browserenv,capture=True)
    assert "not found" not in deps,"browser local closure incomplete"
    print("QA_BROWSER_SETUP=PASS LDD=PASS",flush=True)
(home/'seed.py').write_text("import sys,json,shutil\nfrom pathlib import Path\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.execution import Shot\ncfg,bank,ids,mp4,tests=map(Path,sys.argv[1:])\nsys.path.insert(0,str(tests))\nfrom test_publication import candidate_for\nconfig=load_config(cfg);c=build_core(config)\nowner=c.identity.resolve_identity(config.oidc.issuer,\"owner-a\")\np=c.projects.create_project(owner,\"Historical v5\")\nfolder=config.media_root/p.id;folder.mkdir()\nfor n in (\"camera-a.mp4\",\"camera-b.mp4\",\"master.wav\"):shutil.copyfile(bank/n,folder/n)\n_,a,_=c.media.register_original(owner,p.id,\"video\",\"camera-a.mp4\")\n_,b,_=c.media.register_original(owner,p.id,\"video\",\"camera-b.mp4\")\n_,m,_=c.media.register_original(owner,p.id,\"audio\",\"master.wav\")\nrecipe=c.media.save_recipe(owner,p.id,None,m.id,(Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))\njob=c.jobs.create_job(owner,p.id,\"historical\",recipe.id);ref=c.jobs.claim()\nspec=c.media.prepare_execution(ref);receipt=c.publication.publish(owner,ref,candidate_for(spec,mp4.read_bytes()))\nassert receipt.sha256==\"e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f\"\nids.write_text(json.dumps({\"owner\":owner,\"project\":p.id,\"recipe\":recipe.id,\"job\":job.id,\"sha256\":receipt.sha256}))\nprint(\"HISTORICAL_V5_SEED=PASS\")\n")
(home/'supervisor.py').write_text("import os,sys,subprocess,time,json,resource\nfrom pathlib import Path\n# Manual one-cycle supervisor in the fixed test unit; no auto-retry.\nstart=time.monotonic()\np=subprocess.Popen(sys.argv[1:],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\nassert Path(\"/proc/self/cgroup\").read_text()==Path(f\"/proc/{p.pid}/cgroup\").read_text()\nout,err=p.communicate()\nr=resource.getrusage(resource.RUSAGE_CHILDREN)\npath=Path.home()/\"worker-metrics.jsonl\"\nwith path.open(\"a\") as f:f.write(json.dumps({\"wall_s\":round(time.monotonic()-start,3),\"user_cpu_s\":r.ru_utime,\"system_cpu_s\":r.ru_stime,\"maxrss_kib\":r.ru_maxrss,\"exit\":p.returncode,\"same_test_cgroup\":True})+\"\\n\")\nsys.stdout.write(out)\nsys.stderr.write(err)\nsys.exit(p.returncode if p.returncode>=0 else 128-p.returncode)\n")
(home/'gate.py').write_text("\"\"\"Installed browser -> OIDC test IdP -> HTTP -> real worker CLI -> MP4 gate.\n\nChromium and a test-only HTTPS reverse proxy, not Google or deployed TLS.\nUses one canonical real render. No credentials, private paths or IDs in report.\n\"\"\"\nimport array\nimport base64\nimport hashlib\nfrom http.client import HTTPConnection\nfrom http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\nimport importlib.util\nimport json\nimport os\nfrom pathlib import Path\nimport re\nimport shutil\nimport ssl\nimport subprocess\nimport sys\nimport threading\nimport time\n\nfrom playwright.sync_api import sync_playwright, expect\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.domain.jobs import JobState\nfrom montara_core.adapters.sqlite_db import SQLiteDatabase\nfrom test_oidc_http import TestIdP\n\n\nclass TestProxy:\n    \"\"\"Loopback fixture only: preserve Host/Origin/cookies and all Set-Cookie.\"\"\"\n    def __init__(self, cert, key):\n        proxy=self\n        self.target=None\n        class Handler(BaseHTTPRequestHandler):\n            def forward(self):\n                # Test harness only: exercise Chromium's TLS/download path\n                # before spending a real render, never substitute product MP4.\n                if self.command==\"GET\" and self.path in (\"/__qa-download-page\",\"/__qa-download\"):\n                    probe=self.path==\"/__qa-download\"\n                    payload=b\"qa-transport-probe\" if probe else b'<a href=\"/__qa-download\">Probe</a>'\n                    self.send_response(200)\n                    self.send_header(\"Content-Type\",\"application/octet-stream\" if probe else \"text/html\")\n                    if probe:\n                        self.send_header(\"Content-Disposition\",'attachment; filename=\"probe.bin\"')\n                    self.send_header(\"Content-Length\",str(len(payload)))\n                    self.end_headers()\n                    self.wfile.write(payload)\n                    return\n                conn=HTTPConnection(\"127.0.0.1\",proxy.target,timeout=90)\n                try:\n                    body=self.rfile.read(int(self.headers.get(\"Content-Length\",\"0\"))) if self.command==\"POST\" else None\n                    headers={k:v for k,v in self.headers.items() if k.lower() not in\n                             (\"connection\",\"proxy-connection\",\"transfer-encoding\",\"accept-encoding\")}\n                    conn.request(self.command,self.path,body=body,headers=headers)\n                    result=conn.getresponse()\n                    payload=result.read()\n                    self.send_response(result.status)\n                    for k,v in result.getheaders():\n                        if k.lower() not in (\"server\",\"date\",\"connection\",\"transfer-encoding\",\"content-length\"):\n                            self.send_header(k,v)\n                    self.send_header(\"Content-Length\",str(len(payload)))\n                    self.end_headers()\n                    self.wfile.write(payload)\n                finally:\n                    conn.close()\n            do_GET=forward\n            do_POST=forward\n            def log_message(self,*args): pass\n        self.server=ThreadingHTTPServer((\"127.0.0.1\",0),Handler)\n        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)\n        tls.load_cert_chain(cert,key)\n        self.server.socket=tls.wrap_socket(self.server.socket,server_side=True)\n        self.origin=\"https://localhost:\"+str(self.server.server_port)\n        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)\n        self.thread.start()\n    def close(self):\n        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)\n\n\ndef main():\n    jm,engine,out=map(lambda x:Path(x).resolve(),sys.argv[1:])\n    out.mkdir(mode=0o700)\n    fixture_dir=out/\"idp\"; fixture_dir.mkdir()\n    idp=TestIdP(fixture_dir)\n    idp.browser_enabled=True\n    proxy=TestProxy(fixture_dir/\"test-ca.pem\",fixture_dir/\"test-key.pem\")\n    idp.origin=proxy.origin\n    server=None\n    try:\n        loader=importlib.util.spec_from_file_location(\"jm_fixture\",jm/\"tools/core_v2_media_fixture.py\")\n        media=importlib.util.module_from_spec(loader); loader.loader.exec_module(media)\n        bank=out/\"bank\"; media.generate(bank); before=media.verify_inputs(bank)\n        originals,workspaces,artifacts=[out/n for n in (\"originals\",\"workspaces\",\"artifacts\")]\n        for p in (originals,workspaces,artifacts): p.mkdir(mode=0o700)\n        secret=out/\"client-secret\"; secret.write_text(idp.secret); secret.chmod(0o600)\n        settings=idp.settings(secret)\n        config_path=out/\"core.toml\"\n        config_path.write_text(\"[http]\\nport=0\\n[storage]\\n\"+\n            \"\\n\".join(f\"{k}={json.dumps(str(v))}\" for k,v in {\n                \"sqlite_path\":out/\"core.db\",\"media_root\":originals,\n                \"workspace_root\":workspaces,\"artifact_root\":artifacts}.items())+\n            \"\\n[oidc]\\n\"+\"\\n\".join(f\"{k}={json.dumps(str(getattr(settings,k)))}\" for k in\n                (\"issuer\",\"authorization_endpoint\",\"token_endpoint\",\"jwks_uri\",\"client_id\",\n                 \"client_secret_file\",\"public_origin\"))+\n            '\\n[pilot]\\nallowed_subjects=[\"owner-a\",\"owner-b\"]\\n'+\n            f\"renderer_root={json.dumps(str(jm))}\\nengine_root={json.dumps(str(engine))}\\nrender_timeout_sec=180\\n\")\n        config_path.chmod(0o600)\n        config=load_config(config_path)\n        seed=Path.home()/\"seed.py\"\n        oldcfg=out/\"historical.toml\";oldcfg.write_text(config_path.read_text().split(\"[pilot]\")[0])\n        subprocess.run([os.environ[\"HISTORICAL_PY\"],\"-I\",str(seed),str(oldcfg),str(bank),str(Path.home()/\"historical-ids.json\"),os.environ[\"HISTORICAL_MP4\"],os.environ[\"HISTORICAL_TESTS\"]],check=True,timeout=30)\n        from pathlib import Path as _Path\n        import sqlite3\n        def rows():\n            with sqlite3.connect(config.sqlite_path) as d:\n                assert d.execute(\"PRAGMA integrity_check\").fetchone()==(\"ok\",)\n                assert d.execute(\"PRAGMA foreign_key_check\").fetchall()==[]\n                return {n:sorted(d.execute('SELECT * FROM \"'+n+'\"').fetchall(),key=repr) for n, in d.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\")}\n        historical=rows();inode=config.sqlite_path.stat().st_ino\n        historical_ids=json.loads((Path.home()/\"historical-ids.json\").read_text())\n        assert build_core(config).publication.get_artifact(historical_ids[\"owner\"],historical_ids[\"job\"]).sha256==historical_ids[\"sha256\"]\n        assert rows()==historical and config.sqlite_path.stat().st_ino==inode\n        print(\"HISTORICAL_V5_OPEN_UNCHANGED=PASS\",flush=True)\n        command=[sys.executable,\"-I\",\"-m\",\"montara_core\",\"--config\",str(config_path)]\n        env={**os.environ,\"SSL_CERT_FILE\":str(fixture_dir/\"test-ca.pem\")}\n        server=subprocess.Popen(command,cwd=out,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\n        line=server.stdout.readline()\n        match=re.search(r\"127\\.0\\.0\\.1:(\\d+)\",line)\n        assert match,\"installed HTTP startup failed\"\n        proxy.target=int(match[1])\n\n        # Playwright context ignore_https_errors does not reliably cover\n        # Chromium's separate download network path. Pin only the ephemeral\n        # fixture certificate SPKI in this test browser; trust the fixture CA\n        # in the Node API client. No global certificate bypass in product.\n        cert=fixture_dir/\"test-ca.pem\"\n        pubkey=subprocess.check_output([\"openssl\",\"x509\",\"-in\",str(cert),\"-pubkey\",\"-noout\"],timeout=10)\n        der=subprocess.check_output([\"openssl\",\"pkey\",\"-pubin\",\"-outform\",\"der\"],input=pubkey,timeout=10)\n        pin=base64.b64encode(hashlib.sha256(der).digest()).decode(\"ascii\")\n        os.environ[\"NODE_EXTRA_CA_CERTS\"]=str(cert)\n        with sync_playwright() as playwright:\n            browser=playwright.chromium.launch(channel=\"chromium\",headless=True,\n                args=[\"--ignore-certificate-errors-spki-list=\"+pin],env={**os.environ,\"LD_LIBRARY_PATH\":os.environ[\"QA_LIBPATH\"]})\n            probe_context=browser.new_context(accept_downloads=True)\n            probe_page=probe_context.new_page()\n            probe_page.goto(proxy.origin+\"/__qa-download-page\")\n            with probe_page.expect_download() as transport:\n                probe_page.get_by_role(\"link\",name=\"Probe\").click()\n            assert transport.value.failure() is None,\"QA HTTPS browser download transport failed\"\n            probe_file=out/\"transport-probe.bin\"\n            transport.value.save_as(probe_file)\n            assert probe_file.read_bytes()==b\"qa-transport-probe\"\n            probe_context.close()\n            def login(subject):\n                context=browser.new_context(accept_downloads=True)\n                context.add_cookies([{\"name\":\"fixture-subject\",\"value\":subject,\n                    \"domain\":\"127.0.0.1\",\"path\":\"/authorize\",\"secure\":True,\"sameSite\":\"Lax\"}])\n                page=context.new_page()\n                page.goto(proxy.origin+\"/\")\n                expect(page.locator(\"#login\")).to_be_visible()\n                page.locator(\"#login\").click()\n                if subject==\"denied\":\n                    expect(page.locator(\"body\")).to_contain_text(\"authentication_required\")\n                    return context,page,None\n                expect(page.locator(\"#panel\")).to_be_visible()\n                expect(page.locator(\"#status\")).to_have_text(\"Sesión activa.\")\n                account=page.locator(\"#account\").inner_text()\n                assert re.fullmatch(r\"[0-9a-f-]{36}\",account)\n                session=next(c for c in context.cookies(proxy.origin) if c[\"name\"]==\"__Host-montara-session\")\n                assert session[\"secure\"] and session[\"httpOnly\"] and session[\"sameSite\"]==\"Lax\"\n                return context,page,account\n            denied,_,_=login(\"denied\")\n            denied.close()\n            ca,a,owner_a=login(\"owner-a\")\n            cb,b,owner_b=login(\"owner-b\")\n            assert owner_a!=owner_b and owner_a==historical_ids[\"owner\"]\n            with SQLiteDatabase(config.sqlite_path)._connection() as db:\n                assert db.execute(\"SELECT count(*) FROM oidc_identities\").fetchone()==(2,)\n            a.locator(\"#name\").fill(\"Fixture A\")\n            a.locator(\"#create\").click()\n            expect(a.locator(\"#project-id\")).to_have_text(re.compile(r\"^[0-9a-f-]{36}$\"))\n            expect(a.locator(\"#register\")).to_be_enabled()\n            project=a.locator(\"#project-id\").inner_text()\n            folder=originals/project; folder.mkdir()\n            for name in (\"camera-a.mp4\",\"camera-b.mp4\",\"master.wav\"):\n                shutil.copyfile(bank/name,folder/name)\n            original_hashes={p.name:media.sha256(p) for p in folder.iterdir()}\n            a.locator(\"#register\").click()\n            expect(a.locator(\"#media li\")).to_have_count(3)\n            expect(a.locator(\"#recipe\")).to_be_enabled()\n            a.locator(\"#recipe\").click()\n            expect(a.locator(\"#recipe-state\")).to_contain_text(\"Revisión 1\")\n            expect(a.locator(\"#submit\")).to_be_enabled()\n            a.locator(\"#submit\").click()\n            expect(a.locator(\"#jobs\")).to_contain_text(\"queued\")\n            core=build_core(config)\n            snapshot=core.media.pilot_summary(owner_a,project)\n            job=snapshot[\"jobs\"][0][\"id\"]\n            assert core.jobs.get_job(owner_a,job).state==JobState.QUEUED\n            assert b.request.get(proxy.origin+\"/api/projects/\"+project+\"/pilot\").status==404\n            assert b.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\").status==404\n            assert a.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\").status==409\n            assert a.request.post(proxy.origin+\"/api/claim\",data={},headers={\"Origin\":proxy.origin}).status==404\n\n            # Exactly one real render from installed worker entrypoint; no actor\n            # or job ID passed on its CLI, claim derives owner in SQLite.\n            started=time.monotonic()\n            worker=subprocess.run([sys.executable,\"-I\",str(Path.home()/\"supervisor.py\"),*command,\"--worker-once\"],cwd=out,env=env,\n                                  capture_output=True,text=True,timeout=420)\n            assert worker.returncode==0, \"worker failed: \"+str(worker.returncode)\n            report=json.loads(worker.stdout)\n            assert report[\"worker\"]==\"published\" and report[\"job_id\"]==job\n            expect(a.locator(\"#jobs\")).to_contain_text(\"succeeded\",timeout=15000)\n            # Diagnostic HTTP response must already be authorized and intact;\n            # the actual browser download below remains mandatory.\n            served=a.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\")\n            assert served.status==200 and served.headers[\"content-type\"]==\"video/mp4\"\n            assert \"attachment\" in served.headers[\"content-disposition\"]\n            assert len(served.body())==int(served.headers[\"content-length\"])\n            with a.expect_download() as download:\n                a.get_by_role(\"link\",name=\"Descargar MP4\").click()\n            downloaded=out/\"downloaded.mp4\"\n            assert download.value.failure() is None,\"real browser MP4 download failed\"\n            download.value.save_as(downloaded)\n            assert downloaded.read_bytes()==served.body()\n            receipt=build_core(config).publication.get_artifact(owner_a,job)\n            assert media.sha256(downloaded)==receipt.sha256==media.sha256(receipt.path)\n            assert downloaded.stat().st_size==receipt.size_bytes\n            assert build_core(config).jobs.get_job(owner_a,job).state==JobState.SUCCEEDED\n            assert media.verify_inputs(bank)==before\n            assert {p.name:media.sha256(p) for p in folder.iterdir()}==original_hashes\n            assert b.request.get(proxy.origin+\"/api/jobs/\"+job).status==404\n            assert b.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\").status==404\n            assert b.request.get(proxy.origin+\"/api/projects\").json()==[]\n            fresh=browser.new_context()\n            assert fresh.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\").status==401\n            fresh.close()\n            # Browser reload and owner project selection recover durable job.\n            a.reload(); expect(a.locator(\"#status\")).to_have_text(\"Sesión activa.\")\n            a.locator(\"#projects\").select_option(project)\n            expect(a.locator(\"#jobs\")).to_contain_text(\"succeeded\")\n            a.locator(\"#logout\").click()\n            expect(a.locator(\"#login\")).to_be_visible()\n            assert a.request.get(proxy.origin+\"/api/jobs/\"+job+\"/artifact\").status==401\n            a.locator(\"#login\").click(); expect(a.locator(\"#status\")).to_have_text(\"Sesión activa.\")\n            assert a.locator(\"#account\").inner_text()==owner_a\n            a.locator(\"#projects\").select_option(project)\n            expect(a.locator(\"#jobs\")).to_contain_text(\"succeeded\")\n            idle=subprocess.run([sys.executable,\"-I\",str(Path.home()/\"supervisor.py\"),*command,\"--worker-once\"],cwd=out,env=env,capture_output=True,text=True,timeout=30)\n            assert idle.returncode==0 and json.loads(idle.stdout)[\"worker\"]==\"idle\"\n            ca.close(); cb.close(); browser.close()\n\n        probe=media.probe(downloaded)\n        video=next(s for s in probe[\"streams\"] if s[\"codec_type\"]==\"video\")\n        audio=next(s for s in probe[\"streams\"] if s[\"codec_type\"]==\"audio\")\n        assert (video[\"width\"],video[\"height\"],video[\"avg_frame_rate\"],video[\"codec_name\"],audio[\"codec_name\"])==(1920,1080,\"30/1\",\"h264\",\"aac\")\n        assert abs(float(probe[\"format\"][\"duration\"])-6)<.15\n        media.decode(downloaded)\n        frames=[]\n        for at,color in [(1.5,\"red\"),(2.25,\"blue\"),(2.8,\"blue\"),(3.5,\"yellow\"),(4.5,\"green\")]:\n            actual,rgb=media.frame_color(downloaded,at)\n            assert actual==color,(at,actual,color)\n            frames.append({\"at\":at,\"color\":actual})\n        tones=[]\n        for at in (1,3,5):\n            raw=media.execute([\"ffmpeg\",\"-v\",\"error\",\"-ss\",str(at),\"-i\",str(downloaded),\n                               \"-t\",\"0.25\",\"-vn\",\"-ac\",\"1\",\"-ar\",str(media.RATE),\"-f\",\"s16le\",\"-\"])\n            samples=array.array(\"h\",raw)\n            if sys.byteorder!=\"little\": samples.byteswap()\n            values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}\n            assert values[997]>=50 and values[997]>=10*max(values[223],values[443])\n            tones.append({\"at\":at,\"master_dominant\":True})\n        evidence={\"status\":\"PASS\",\"scope\":\"Chromium HTTPS fixture OIDC -> installed Core HTTP -> installed worker CLI -> real Engine -> publication -> browser MP4 download\",\n                  \"browser\":\"Chromium\",\"browser_transport_preflight\":\"PASS\",\"admission\":\"PASS\",\"owner_isolation\":\"PASS\",\"logout_relogin\":\"PASS\",\n                  \"worker\":\"PASS\",\"worker_idle_no_duplicate\":\"PASS\",\"publication\":\"PASS\",\n                  \"published_reopen\":\"PASS\",\"browser_download_digest\":\"PASS\",\"full_decode\":\"PASS\",\n                  \"duration_sec\":float(probe[\"format\"][\"duration\"]),\"resolution\":\"1920x1080\",\"fps\":30,\n                  \"video_codec\":\"h264\",\"audio_codec\":\"aac\",\"sha256\":receipt.sha256,\"size_bytes\":receipt.size_bytes,\n                  \"frames\":frames,\"tones\":tones,\"originals_unchanged\":True,\"google_real\":\"NOT_RUN\",\n                  \"production_proxy\":\"NOT_RUN\"}\n        assert config.sqlite_path.stat().st_ino==inode\n        after=rows()\n        assert all(all(row in after[n] for row in oldrows) for n,oldrows in historical.items())\n        assert build_core(config).publication.get_artifact(historical_ids[\"owner\"],historical_ids[\"job\"]).sha256==historical_ids[\"sha256\"]\n        (Path.home()/\"pilot-ids.json\").write_text(json.dumps({\"owner\":owner_a,\"project\":project,\"job\":job,\"recipe\":snapshot[\"recipe\"][\"id\"],\"receipt\":receipt.sha256,\"historical\":historical_ids,\"historical_tables\":len(historical),\"historical_rows\":sum(map(len,historical.values()))}))\n        evidence[\"historical_preserved\"]=True\n        evidence[\"supervisor_single_cycle\"]=True\n        (out/\"quality-report.json\").write_text(json.dumps(evidence,indent=2))\n        print(json.dumps(evidence,indent=2))\n    finally:\n        if server is not None:\n            server.terminate()\n            try: server.wait(timeout=10)\n            except subprocess.TimeoutExpired:\n                server.kill(); server.wait()\n        proxy.close(); idp.close()\n\n\nif __name__==\"__main__\":\n    main()\n")
(home/'recovery.py').write_text("import sys,json,sqlite3,subprocess,os,hashlib,fcntl\nfrom pathlib import Path\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.bootstrap.worker import worker_lock\nfrom montara_core.domain.jobs import JobState\nfrom montara_core.application.ports import StorageError\ncfg,ids,mp4,tests,out=map(Path,sys.argv[1:])\nsys.path.insert(0,str(tests))\nfrom test_publication import candidate_for\nconfig=load_config(cfg);d=json.loads(ids.read_text());c=build_core(config)\nowner=d[\"owner\"];project=d[\"project\"];recipe=d[\"recipe\"]\ncmd=[sys.executable,\"-I\",\"-m\",\"montara_core\",\"--config\",str(cfg)]\ndef rows():\n with sqlite3.connect(config.sqlite_path) as db:\n  assert db.execute(\"PRAGMA integrity_check\").fetchone()==(\"ok\",)\n  assert db.execute(\"PRAGMA foreign_key_check\").fetchall()==[]\n  return {n:sorted(db.execute('SELECT * FROM \"'+n+'\"').fetchall(),key=repr) for n, in db.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\")}\ndef cli(*args,code,state):\n p=subprocess.run(cmd+list(args),capture_output=True,text=True,timeout=30)\n assert p.returncode==code,(p.returncode,state)\n assert json.loads(p.stdout)[\"worker\"]==state\ndef check_receipts():\n for j,sha in [(d[\"job\"],d[\"receipt\"]),(d[\"historical\"][\"job\"],d[\"historical\"][\"sha256\"])]:\n  receipt=build_core(config).publication.get_artifact(owner,j)\n  assert receipt.sha256==sha==hashlib.sha256(receipt.path.read_bytes()).hexdigest()\nbefore=rows();check_receipts()\nlock=config.sqlite_path.with_name(config.sqlite_path.name+\".worker.lock\")\ninode=lock.stat().st_ino\nwith worker_lock(config):\n cli(\"--worker-once\",code=3,state=\"worker_busy\")\n cli(\"--worker-recover\",\"00000000-0000-4000-8000-000000000000\",code=3,state=\"worker_busy\")\n assert rows()==before\nassert lock.stat().st_ino==inode\n# Create a candidate in a separate process, then lose that process before publication.\njob=c.jobs.create_job(owner,project,\"recovery-fixture\",recipe)\ncode=\"\"\"import sys,os\nfrom pathlib import Path\nfrom montara_core.bootstrap.config import load_config\nfrom montara_core.bootstrap.core import build_core\nfrom montara_core.bootstrap.worker import worker_lock\ncfg,mp4,tests=map(Path,sys.argv[1:]);sys.path.insert(0,str(tests))\nfrom test_publication import candidate_for\nconfig=load_config(cfg)\nwith worker_lock(config):\n c=build_core(config);claim=c.jobs.claim_export();spec=c.media.prepare_execution(claim.attempt)\n candidate_for(spec,mp4.read_bytes())\n os._exit(23)\n\"\"\"\np=subprocess.run([sys.executable,\"-I\",\"-c\",code,str(cfg),str(mp4),str(tests)],timeout=30)\nassert p.returncode==23\nclaim=c.jobs.running_export();ref=claim.attempt\nassert ref.job_id==job.id\nbefore=rows();cli(\"--worker-once\",code=3,state=\"attempt_pending\");assert rows()==before\ncli(\"--worker-recover\",\"00000000-0000-4000-8000-000000000000\",code=3,state=\"attempt_not_current\")\nassert rows()==before\ncli(\"--worker-recover\",ref.id,code=0,state=\"recovered\")\nreceipt=c.publication.get_artifact(owner,job.id);assert receipt.sha256==d[\"receipt\"]\nbefore=rows();cli(\"--worker-recover\",ref.id,code=0,state=\"recovered\");assert rows()==before\ncli(\"--worker-interrupt\",ref.id,code=3,state=\"attempt_not_current\");assert rows()==before\nj=c.jobs.create_job(owner,project,\"interrupt-fixture\",recipe);claim=c.jobs.claim_export();ref2=claim.attempt\nassert ref2.job_id==j.id\n# No candidate, no executor spawned, lock released: recover must not invent a result.\ncli(\"--worker-recover\",ref2.id,code=5,state=\"recovery_required\")\nassert c.jobs.get_job(owner,j.id).state==JobState.RUNNING\ncli(\"--worker-interrupt\",ref2.id,code=0,state=\"interrupted\")\nassert c.jobs.get_job(owner,j.id).state==JobState.FAILED\ncli(\"--worker-once\",code=0,state=\"idle\");check_receipts()\nassert lock.stat().st_ino==inode\nout.write_text(json.dumps({\"busy_no_mutation\":True,\"lock_inode_preserved\":True,\"candidate_process_exit\":23,\"exact_recover_without_render\":True,\"replay_idempotent\":True,\"stale_interrupt_rejected\":True,\"nonrecoverable_active_interrupted\":True,\"idle_no_retry\":True,\"historical_and_real_receipts_preserved\":True,\"scope\":\"candidate fixture attestation with archived real bytes; no new render; no concurrent executor during interrupt\"},indent=2))\nprint(\"RECOVERY_CLI=PASS\")\n")
os.environ.update(HISTORICAL_PY=str(oldexe),HISTORICAL_MP4=str(mp4),HISTORICAL_TESTS=str(home/"source-historical-v5/tests"))
pilot=home/"pilot-state"
with phase("browser_supervised_gate",pilot):
    run(exe,"-I","-c","import runpy,sys;sys.path.insert(0,sys.argv.pop(1));sys.path.insert(0,sys.argv.pop(1));runpy.run_path(sys.argv.pop(1),run_name='__main__')",qa,repo/"tests",home/"gate.py",renderer,eng,pilot,cwd=outside,env=dict(os.environ,LD_LIBRARY_PATH=os.environ["QA_LIBPATH"]))
    assert json.loads((pilot/"quality-report.json").read_text())["status"]=="PASS"
    shutil.copyfile(pilot/"quality-report.json",out/"quality-report.json")
    save("worker-metrics.json",[json.loads(l) for l in (home/"worker-metrics.jsonl").read_text().splitlines()])
with phase("worker_recovery",pilot):
    run(exe,"-I",home/"recovery.py",pilot/"core.toml",home/"pilot-ids.json",mp4,repo/"tests",out/"recovery.json",cwd=outside)
    # Check-config after state change, with external TOML. No server writes after gate.
    run(exe,"-I","-m","montara_core","--config",pilot/"core.toml","--check-config",cwd=outside)
    assert not (pilot/"core.db").is_symlink()
    with sqlite3.connect(pilot/"core.db") as db:
        assert db.execute("PRAGMA user_version").fetchone()==(5,)
        assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall()==[]
ids=json.loads((home/"pilot-ids.json").read_text())
shutil.copyfile(wa,out/wa.name);shutil.copyfile(pilot/"downloaded.mp4",out/"downloaded-pilot.mp4")
save("manifest.json",{"format":1,"core":ma,"historical_v5":mh,"sources":{"renderer":jm,"engine":engine},"double_build_equal":True,"python":"3.12.12","locks":{"python":sha(out/"python-lock.json"),"ffmpeg":sha(out/"ffmpeg-lock.json")},"historical_tables":ids["historical_tables"],"historical_rows":ids["historical_rows"],"historical_state_reconstructed":True,"historical_receipt_preserved":True,"ui_resources":True,"installed_suite":100,"suite_reused_from_first_attempt":os.environ.get("H2_REUSE_SUITE")=="1","browser_gate":True,"recovery":True,"worker_supervisor":"one cycle in existing test cgroup; dedicated stop not accredited","cgroup_peak_bytes":cnum("memory.peak"),"limits":{"memory_max_bytes":cnum("memory.max"),"pids_max":cnum("pids.max"),"cpu_affinity":len(os.sched_getaffinity(0))},"installed_slot_bytes":size(exe.parent.parent),"qa_browser_bytes":size(home/"browsers"),"disk_free_bytes":shutil.disk_usage(home).free,"google_real":"NOT RUN"})
print("H2_30_GATE_RESULT=PASS",flush=True)
PY
python3 "$HOME/release.py"
