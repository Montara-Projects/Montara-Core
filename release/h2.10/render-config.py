"""Prepare private configuration only. No services, network, DB writes or API calls."""
import argparse
import json
import os
import re
import stat
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

def private_text(path):
    p = Path(path)
    if not p.is_absolute() or p.resolve() != p or not p.is_file():
        raise ValueError("private file must be canonical and regular")
    if p.stat().st_mode & 0o077:
        raise ValueError("private input file must not be group/world accessible")
    return p.read_text(encoding="utf-8").strip()

def line(s):
    if not isinstance(s, str) or not s or any(ord(c)<33 or ord(c)>126 for c in s):
        raise ValueError("invalid private identifier")
    return s

def generate(d):
    if d["format"] != 1 or d["environment"] != "DEVELOPMENT":
        raise ValueError("wrong deployment profile")
    origin = d["public_origin"]
    u = urlsplit(origin)
    if (u.scheme != "https" or not u.hostname or origin != "https://"+u.hostname
            or not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev",u.hostname)
            or u.hostname == "montara.example.workers.dev"):
        raise ValueError("confirm exact assigned workers.dev hostname first")
    ingress=d["ingress"]
    if ingress["adapter"] != "cloudflare-vpc-access" or ingress["vpc_binding"] != "CORE":
        raise ValueError("only reviewed Access profile; never fallback to open")
    ident=d["identity"]
    expected={"issuer":"https://accounts.google.com","authorization_endpoint":"https://accounts.google.com/o/oauth2/v2/auth","token_endpoint":"https://oauth2.googleapis.com/token","jwks_uri":"https://www.googleapis.com/oauth2/v3/certs"}
    if any(ident[k]!=v for k,v in expected.items()):
        raise ValueError("Google discovery mismatch; review explicitly")
    client=line(private_text(ident["client_id_file"]))
    subs=[line(x) for x in private_text(ident["allowed_subjects_file"]).splitlines()]
    if not 1<=len(subs)<=4 or len(set(subs))!=len(subs) or any(len(x)>255 for x in subs):
        raise ValueError("one to four distinct verified subjects required")
    aud=line(private_text(ingress["access_audience_file"]))
    if not re.fullmatch(r"[0-9a-f]{64}",aud):
        raise ValueError("invalid Access audience")
    if ident["client_secret_file"]!="/etc/montara-pilot/client-secret":
        raise ValueError("invalid secret path")
    state=Path(d["storage"]["state_root"])
    slot=Path(d["release"]["slot"])
    port=d["http"]["port"]
    w=d["worker"]
    if (state!=Path("/var/lib/montara-pilot") or slot!=Path("/opt/montara-pilot/releases/c7e11118d2d1")
        or port!=8080 or w!={"render_timeout_sec":180,"memory_max_bytes":4294967296,"cpu_quota_percent":200,"tasks_max":256,"runtime_max_sec":600}):
        raise ValueError("deployment paths or limits differ from approved template")
    # JSON strings/arrays with printable ASCII are valid TOML strings/arrays.
    sections={"http":{"port":port},"storage":{"sqlite_path":str(state/"db/core.sqlite"),"media_root":str(state/"originals"),"workspace_root":str(state/"attempts"),"artifact_root":str(state/"artifacts")},"oidc":dict(expected,client_id=client,client_secret_file=ident["client_secret_file"],public_origin=origin),"pilot":{"allowed_subjects":subs,"renderer_root":str(slot/"renderer-src"),"engine_root":str(slot/"engine-src"),"render_timeout_sec":w["render_timeout_sec"]}}
    core="\n".join("["+k+"]\n"+"\n".join(n+" = "+json.dumps(v,ensure_ascii=True) for n,v in a.items())+"\n" for k,a in sections.items())
    assert tomllib.loads(core)==sections
    edge={"PUBLIC_ORIGIN":origin,"ACCESS_AUD":aud}
    checklist={"public_origin":origin,"host":u.hostname,"redirect_uri":origin+"/auth/callback","backend":f"127.0.0.1:{port}","scope":"openid"}
    return core,edge,checklist

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("private_toml",type=Path)
    ap.add_argument("output_dir",type=Path)
    args=ap.parse_args()
    os.umask(0o077)
    d=tomllib.loads(private_text(args.private_toml))
    core,edge,checklist=generate(d)
    # Fresh private directory only. Operator installs files explicitly later.
    args.output_dir.mkdir(mode=0o700)
    for name,content in (("core.toml",core),("edge-vars.json",json.dumps(edge,indent=2)),("oauth-checklist.json",json.dumps(checklist,indent=2))):
        with (args.output_dir/name).open("x",encoding="utf-8") as f:
            f.write(content+"\n")
    print("PRIVATE_CONFIG_PREPARED=PASS")
if __name__=="__main__":
    main()
