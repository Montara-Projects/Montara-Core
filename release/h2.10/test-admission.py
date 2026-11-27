"""Focused admission delta only: no Google, Core wheel, Engine, root or services."""
import copy
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import tomllib

here=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("extension",here/"extend-admission.py")
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
r=e.render
os.umask(0o077)

def rejects(fn):
    try: fn()
    except ValueError: return
    raise AssertionError("unexpected acceptance")

with tempfile.TemporaryDirectory() as name:
    p=Path(name).resolve()
    d=tomllib.loads((here/"pilot.example.toml").read_text())
    d["public_origin"]="https://montara.fixture.workers.dev"
    def put(n,content):
        f=p/n;f.write_text(content);f.chmod(0o600);return str(f)
    d["identity"]["client_id_file"]=put("client-id","fixture-client")
    d["ingress"]["access_audience_file"]=put("aud","a"*64)
    oldfile=p/"old"; d["identity"]["allowed_subjects_file"]=str(oldfile)
    def subjects(f,seq):
        f.write_text("".join(s+"\n" for s in seq));f.chmod(0o600)
    subs=["fixture-a","fixture-b","fixture-c","fixture-d","fixture-e"]
    for n in range(1,5):
        subjects(oldfile,subs[:n])
        core,_,_=r.generate(d)
        assert tomllib.loads(core)["pilot"]["allowed_subjects"]==subs[:n]
    for invalid in ([],subs,["fixture-a","fixture-a"]):
        subjects(oldfile,invalid);rejects(lambda:r.generate(d))
    print("CONFIG_ONE_TO_FOUR_AND_LIMITS=PASS")
    subjects(oldfile,subs[:2])
    original,edge,checklist=r.generate(d)
    current=tomllib.loads(original)
    candidate=p/"candidate";subjects(candidate,subs[:3])
    oldbytes=oldfile.read_bytes()
    new=e.extend(d,current,candidate);new3=tomllib.loads(new)
    assert new3["pilot"]["allowed_subjects"]==subs[:3]
    same=copy.deepcopy(new3);same["pilot"]["allowed_subjects"]=subs[:2]
    assert same==current and oldfile.read_bytes()==oldbytes
    subjects(candidate,subs[:4])
    new4=tomllib.loads(e.extend(d,current,candidate))
    assert new4["pilot"]["allowed_subjects"]==subs[:4]
    subjects(oldfile,subs[:3])
    assert tomllib.loads(e.extend(d,new3,candidate))==new4
    subjects(oldfile,subs[:2])
    print("APPEND_2_TO_3_TO_4_AND_2_TO_4=PASS")
    for invalid in (subs[:1],subs[:2],["fixture-b","fixture-a","fixture-c"],["fixture-a","fixture-c","fixture-d"],["fixture-a","fixture-b","fixture-b"],subs):
        subjects(candidate,invalid);rejects(lambda:e.extend(d,current,candidate))
    subjects(candidate,subs[:3])
    changed=copy.deepcopy(current);changed["http"]["port"]=8090
    rejects(lambda:e.extend(d,changed,candidate))
    subjects(oldfile,subs[:3]);rejects(lambda:e.extend(d,current,candidate))
    subjects(oldfile,subs[:2])
    assert oldfile.read_bytes()==oldbytes
    print("REMOVAL_REORDER_DUPLICATE_OVERFLOW_CONFIG_DRIFT_REJECTED=PASS")

    # Read-only state fingerprint from the actual new maintenance script, on a
    # small synthetic DB/receipt inventory, not the product schema or a render.
    state=p/"state";state.mkdir()
    for n in ("db","originals","attempts","artifacts"):(state/n).mkdir()
    db=state/"db/core.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE fixture_identity (subject text, uuid text)")
        c.execute("INSERT INTO fixture_identity VALUES ('fixture-a','persistent-uuid')")
    (state/"db/core.sqlite.worker.lock").touch(mode=0o600)
    artifact=state/"artifacts/fixture.mp4";artifact.write_bytes(b"synthetic bytes; not a media render")
    code=(here/"add-subjects.sh").read_text().split('snapshot() {',1)[1].split("<<'PY'\n",1)[1].split("\nPY\n}",1)[0]
    def fingerprint():
        return subprocess.check_output(["python3","-I","-c",code,str(state)],text=True)
    before=fingerprint();dbbytes=db.read_bytes();dbinode=db.stat().st_ino
    subjects(candidate,subs[:4])
    newtext=e.extend(d,current,candidate)
    # Simulate only the same-filesystem two-file config publication in scratch.
    active=p/"core.toml";active.write_text(original+"\n")
    backup=p/"old-core";backup.write_bytes(active.read_bytes())
    pending=p/"new-core";pending.write_text(newtext+"\n")
    os.replace(candidate,oldfile);os.replace(pending,active)
    assert tomllib.loads(active.read_text())==new4
    assert backup.read_text()==original+"\n"
    assert oldfile.read_text().splitlines()==subs[:4]
    assert fingerprint()==before and db.stat().st_ino==dbinode and db.read_bytes()==dbbytes
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True) as c:
        assert c.execute("SELECT uuid FROM fixture_identity").fetchall()==[("persistent-uuid",)]
    assert artifact.read_bytes()==b"synthetic bytes; not a media render"
    print("CONFIG_SWAP_PRESERVES_FIXTURE_DB_UUID_ARTIFACT=PASS")
    artifact.write_bytes(b"changed fixture")
    assert fingerprint()!=before
    print("STATE_FINGERPRINT_DETECTS_CHANGE=PASS")
print("ADMISSION_DELTA_MICROGATE=PASS")
