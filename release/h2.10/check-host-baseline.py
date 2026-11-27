"""Check exact artifacts and explicit OS baseline; no automatic hash acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

def sha(p):
    with p.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def run(*args, env=None):
    p = subprocess.run([str(x) for x in args], env=env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
    return p.stdout.strip()

def git_blob(p):
    b = p.read_bytes()
    return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()

def baseline(slot, delta):
    oldfile = slot/"core-src/release/manifests/h2.6-ffmpeg-lock.json"
    assert git_blob(oldfile)==delta["historical_lock_git_blob"]=="f425eed4a28ef2c5bb60a042c0ffbfe9d11b59e6"
    old=json.loads(oldfile.read_text())
    expected=dict(old["host_library_sha256"])
    assert len(expected)==delta["host_library_count"]==75
    changes=delta["changes"]
    assert len(changes)==len({x["path"] for x in changes})==4
    for x in changes:
        assert expected[x["path"]]==x["old_sha256"]
        expected[x["path"]]=x["sha256"]
        version=run("dpkg-query","-W","-f=${Version}",x["package"])
        assert version==delta["package_version"],"host security package version drift"
    root=slot/"ffmpeg-root"
    assert root.resolve()==root
    libdirs=[root/"usr/lib/x86_64-linux-gnu",root/"lib/x86_64-linux-gnu"]
    libdirs+=sorted({p.parent for p in root.rglob("*.so*") if p.is_file()}-set(libdirs))
    libpath=":".join(map(str,libdirs))
    env=dict(os.environ,LD_LIBRARY_PATH=libpath,LC_ALL="C")
    actual={}
    for n in ("ffmpeg","ffprobe"):
        real=root/"usr/bin"/n
        assert real.resolve().is_relative_to(root) and sha(real)==old["binary_sha256"][n]
        wrapper=slot/"media-tools"/n
        assert not wrapper.is_symlink() and wrapper.read_text()=='#!/bin/sh\nexport LD_LIBRARY_PATH="'+libpath+'"\nexec "'+str(real)+'" "$@"\n'
        lines=run("ldd","-r",real,env=env).splitlines()
        assert not any("not found" in line or "undefined symbol" in line for line in lines)
        for line in lines:
            m=re.search(r"(?:=> )?(/[^ ]+)",line)
            if m:
                p=Path(m.group(1))
                assert p.is_file()
                if p.is_relative_to(root):
                    assert p.resolve().is_relative_to(root),"local library escapes extracted tree"
                else:
                    actual[str(p)]=sha(p)
    diff={"added":sorted(set(actual)-set(old["host_library_sha256"])),
          "removed":sorted(set(old["host_library_sha256"])-set(actual)),
          "changed":[{"path":n,"sha256":v} for n,v in sorted(actual.items()) if old["host_library_sha256"].get(n)!=v]}
    print("HOST_RESOLUTION_DIFF="+json.dumps(diff,sort_keys=True),flush=True)
    assert actual==expected,"unreviewed host baseline drift"
    print("EXACT_BINARIES_AND_HOST_BASELINE=PASS",flush=True)
    assert "6.1.1" in run(slot/"media-tools/ffmpeg","-version").splitlines()[0]
    assert "6.1.1" in run(slot/"media-tools/ffprobe","-version").splitlines()[0]
    enc=run(slot/"media-tools/ffmpeg","-hide_banner","-encoders")
    assert "libx264" in enc and " aac " in enc
    print("HOST_RELOCATIONS_AND_ENCODERS=PASS",flush=True)
    return old

def x11_link(slot, old, normalize=False):
    # Only this one exact packaged absolute symlink is eligible for relocation.
    # If the target is not present in the original archive, stop; do not use /etc.
    root=slot/"ffmpeg-root"
    link=root/"usr/share/X11/rgb.txt"
    target=root/"etc/X11/rgb.txt"
    assert link.is_symlink()
    relative=os.path.relpath(target,link.parent)
    assert os.readlink(link) in ("/etc/X11/rgb.txt",relative)
    entries=[x for x in old["debs"] if x["filename"].startswith("x11-common_")]
    assert len(entries)==1
    x=entries[0]
    deb=slot/"debs"/x["filename"]
    assert deb.stat().st_size==x["size"] and sha(deb)==x["sha256"]
    content=None; original_link=None
    with subprocess.Popen(["dpkg-deb","--fsys-tarfile",str(deb)],stdout=subprocess.PIPE) as p:
        with tarfile.open(fileobj=p.stdout,mode="r|") as t:
            for m in t:
                name=m.name.removeprefix("./")
                if name=="etc/X11/rgb.txt" and m.isfile():
                    content=t.extractfile(m).read()
                if name=="usr/share/X11/rgb.txt" and m.issym():
                    original_link=m.linkname
        assert p.wait()==0
    print("X11_ARCHIVE_TARGET_PRESENT="+str(content is not None),flush=True)
    assert original_link=="/etc/X11/rgb.txt" and content is not None
    assert target.is_file() and not target.is_symlink() and target.read_bytes()==content
    assert target.resolve().is_relative_to(root)
    if normalize and os.readlink(link)!=relative:
        # Atomic replacement of one symlink in the private unsealed tree.
        tmp=link.with_name(".rgb.txt.montara-relative")
        tmp.symlink_to(relative)
        os.replace(tmp,link)
    print("X11_LOCAL_RELOCATION="+("PASS" if os.readlink(link)==relative else "REQUIRED"),flush=True)

def symlink_audit(slot):
    issues=[]
    for p in slot.rglob("*"):
        if not p.is_symlink():continue
        target=p.resolve(strict=False)
        if target.is_relative_to(slot):continue
        ok=target.exists() and any(target.is_relative_to(Path(x)) for x in ("/usr/lib","/usr/bin","/lib","/lib64"))
        if ok:
            ok=all(q.stat().st_uid==0 and not q.stat().st_mode&0o022 for q in (target,*target.parents))
        if not ok:issues.append([str(p.relative_to(slot)),os.readlink(p)])
    print("EXTERNAL_SYMLINK_ISSUES="+json.dumps(issues),flush=True)
    return issues

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("slot",type=Path)
    ap.add_argument("delta",type=Path)
    ap.add_argument("--normalize-x11",action="store_true")
    a=ap.parse_args()
    assert a.slot.resolve()==a.slot and os.getuid()!=0
    d=json.loads(a.delta.read_text())
    old=baseline(a.slot,d)
    x11_link(a.slot,old,a.normalize_x11)
    issues=symlink_audit(a.slot)
    if a.normalize_x11: assert not issues
    else: assert all(x==["ffmpeg-root/usr/share/X11/rgb.txt","/etc/X11/rgb.txt"] for x in issues)
    print("BASELINE_PREFLIGHT=PASS",flush=True)

if __name__=="__main__":
    main()
