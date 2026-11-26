"""Explicit Linux real-engine gate using JM's external fixture and oracle."""
import importlib.util
import json
from pathlib import Path
import sys
from dataclasses import replace
import shutil
import array
import math
import os
import subprocess
import wave
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import ExecutionSpec, EngineCandidate, Shot
from montara_core.domain.jobs import JobState
from montara_core.domain.publication import ArtifactReceipt
from montara_core.adapters.zucker_engine import ZuckerEngineAdapter, EngineExecutionError, _stage_semantics


def main():
    jm, engine, out = map(lambda x: Path(x).resolve(), sys.argv[1:])
    out.mkdir(mode=0o700)
    loader = importlib.util.spec_from_file_location("jm_fixture", jm/"tools/core_v2_media_fixture.py")
    media = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(media)
    bank=out/"bank"
    media.generate(bank)
    original_manifest=media.verify_inputs(bank)
    originals=out/"originals"
    workspaces=out/"workspaces"
    artifacts=out/"artifacts"
    originals.mkdir(); workspaces.mkdir(); artifacts.mkdir()
    config=Config(sqlite_path=out/"core.db",media_root=originals,workspace_root=workspaces,artifact_root=artifacts)
    core=build_core(config)
    owner=core.projects.provision_account().id

    def prepare(name, *, short_master=False, short_camera=False, false_master=False, silent_master=False):
        project=core.projects.create_project(owner,name).id
        folder=originals/project
        folder.mkdir()
        for filename in ("camera-a.mp4","camera-b.mp4","master.wav"):
            shutil.copyfile(bank/filename,folder/filename)
        if short_master:
            (folder/"master.wav").unlink()
            media.execute(["ffmpeg","-v","error","-i",str(bank/"master.wav"),"-t","5.9",
                           "-c:a","pcm_s16le",str(folder/"master.wav")])
        if false_master:
            shutil.copyfile(bank/"camera-a.mp4",folder/"master.wav")
        if silent_master:
            # Authored silence is real PCM coverage, not missing audio.
            master=folder/"master.wav"
            with wave.open(str(master),"rb") as f:
                params=f.getparams(); data=f.readframes(f.getnframes())
            assert params.nframes==6*params.framerate
            stride=params.nchannels*params.sampwidth
            tail=params.framerate*stride
            with wave.open(str(master),"wb") as f:
                f.setparams(params); f.writeframes(data[:-tail]+bytes(tail))
        if short_camera:
            # Misleading container duration: video ends at 5.9, audio at 6.
            short=out/"short-video.mp4"
            media.execute(["ffmpeg","-v","error","-i",str(bank/"camera-a.mp4"),"-t","5.9",
                           "-an","-c:v","libx264","-threads","2",str(short)])
            (folder/"camera-a.mp4").unlink()
            media.execute(["ffmpeg","-v","error","-i",str(short),"-i",str(bank/"camera-a.mp4"),
                           "-map","0:v:0","-map","1:a:0","-c","copy",str(folder/"camera-a.mp4")])
            assert float(media.probe(folder/"camera-a.mp4")["format"]["duration"])>=6.0
        _,a,_=core.media.register_original(owner,project,"video","camera-a.mp4")
        _,b,_=core.media.register_original(owner,project,"video","camera-b.mp4")
        _,m,_=core.media.register_original(owner,project,"audio","master.wav")
        recipe=core.media.save_recipe(owner,project,None,m.id,
                    (Shot(a.id,0,2000),Shot(b.id,2000,4000),Shot(a.id,4000,6000)))
        job=core.jobs.create_job(owner,project,"export",recipe.id)
        ref=core.jobs.claim()
        assert ref.job_id==job.id
        spec=core.media.prepare_execution(ref)
        assert isinstance(spec,ExecutionSpec) and spec.recipe_revision_id==recipe.id
        assert {p.path for p in spec.inputs}==set(spec.workspace.iterdir())
        return spec,ref,job,folder

    adapter=ZuckerEngineAdapter(jm,engine)
    negatives={}
    for name,opts,expected in [
        ("short-master",{"short_master":True},"Master does not cover"),
        ("short-camera",{"short_camera":True},"Camera does not cover"),
        ("false-master",{"false_master":True},"H2 master requires"),
    ]:
        bad,ref,_,_=prepare(name,**opts)
        try: adapter.execute(bad)
        except EngineExecutionError as exc:
            assert expected in str(exc),(name,str(exc))
        else: raise AssertionError(name+" accepted")
        assert not (bad.workspace/"engine"/"candidate.mp4").exists()
        assert not (bad.workspace/"engine"/"request.json").exists()
        assert all(media.sha256(i.path)==i.sha256 for i in bad.inputs)
        try: adapter.execute(bad)
        except EngineExecutionError: pass
        else: raise AssertionError("Partial workspace reuse accepted")
        core.jobs.fail(ref)
        negatives[name]="PASS"
    def samples_at(path, at):
        raw=media.execute(["ffmpeg","-v","error","-ss",str(at),"-i",str(path),
                           "-t","0.25","-vn","-ac","1","-ar",str(media.RATE),"-f","s16le","-"])
        values=array.array("h",raw)
        if sys.byteorder!="little": values.byteswap()
        assert len(values)>=media.RATE//8
        return values

    def rms(values):
        return math.sqrt(sum(v*v for v in values)/len(values))

    def verify_no_duplicate(spec):
        root=spec.workspace/"engine"
        request=json.loads((root/"request.json").read_text())
        expected={str(i.path) for i in spec.inputs}
        assert set(request["source_files"].values())|{request["master"]}==expected
        assert not (root/"inputs").exists()
        assert all(media.sha256(i.path)==i.sha256 for i in spec.inputs)

    # Demonstrate the actual pinned mux's intentional presentation padding
    # with nonzero selected audio offset, 1s bookends, 4s content.
    mux_root=out/"legacy-mux"
    mux_root.mkdir()
    joined=mux_root/"video.mp4"
    media.execute(["ffmpeg","-v","error","-i",str(bank/"camera-a.mp4"),"-an","-c:v","copy",str(joined)])
    mux_output=mux_root/"candidate.mp4"
    mux_code=(
        "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
        "from core.stages.export import _mux_continuous_master_audio as mux; "
        "mux(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]),1.0,6.0,1000000,"
        "lambda *args:None,content_start=1.0,content_end=5.0,audio_delay=1.0)"
    )
    mux_env={"PATH":os.environ["PATH"],"HOME":str(mux_root),
             "ZUCKER_DATA_ROOT":str(mux_root/"state"),"ZUCKER_GLOBAL_CACHE":str(mux_root/"cache"),
             "PYTHONDONTWRITEBYTECODE":"1","LANG":"C.UTF-8"}
    subprocess.run([sys.executable,"-I","-B","-c",mux_code,str(engine),str(joined),
                    str(bank/"master.wav"),str(mux_output)],env=mux_env,cwd=mux_root,
                   check=True,capture_output=True,timeout=180)
    media.decode(mux_output)
    assert abs(float(media.probe(mux_output)["format"]["duration"])-6)<1/30
    intro,content,outro=[rms(samples_at(mux_output,at)) for at in (.25,2.75,5.5)]
    assert content>100 and intro<content*.01 and outro<content*.01,(intro,content,outro)

    silent,sref,sjob,sfolder=prepare("authored-silence",silent_master=True)
    before={p.name:media.sha256(p) for p in sfolder.iterdir()}
    silent_candidate=adapter.execute(silent)
    verify_no_duplicate(silent)
    assert {p.name:media.sha256(p) for p in sfolder.iterdir()}==before
    assert core.jobs.get_job(owner,sjob.id).state==JobState.RUNNING
    active=rms(samples_at(silent_candidate.path,3.0))
    quiet=rms(samples_at(silent_candidate.path,5.5))
    assert active>100 and quiet<active*.01,(active,quiet)
    assert abs(float(media.probe(silent_candidate.path)["format"]["duration"])-6)<1/30
    # Publish the valid authored-silence result too; not a synthetic success.
    silent_receipt=core.publication.publish(owner,sref,silent_candidate)
    assert core.jobs.get_job(owner,sjob.id).state==JobState.SUCCEEDED
    assert media.sha256(silent_receipt.path)==silent_candidate.sha256

    spec,ref,job,folder=prepare("positive")
    staged_before={p.path:media.sha256(p.path) for p in spec.inputs}
    original_before={p.name:media.sha256(p) for p in folder.iterdir()}
    extra=spec.workspace/"extra.txt";extra.write_text("not authorized")
    try: adapter.execute(spec)
    except EngineExecutionError: pass
    else: raise AssertionError("Unexpected workspace member accepted")
    extra.unlink()
    foreign=replace(spec.inputs[0],path=bank/"camera-a.mp4")
    try: adapter.execute(replace(spec,inputs=(foreign,*spec.inputs[1:])))
    except EngineExecutionError: pass
    else: raise AssertionError("Foreign staged path accepted")
    assert not (spec.workspace/"engine").exists()
    negatives.update(extra_workspace="PASS",foreign_path="PASS")
    candidate=adapter.execute(spec)
    assert isinstance(candidate,EngineCandidate)
    verify_no_duplicate(spec)
    result=vars(candidate)
    workspace=spec.workspace
    attestation=json.loads((workspace/"engine"/"candidate-manifest.json").read_text())
    assert attestation["schema"]==2
    assert attestation["job_id"]==job.id and attestation["task_id"]==ref.task_id
    assert attestation["snapshot_sha256"]==spec.snapshot_sha256
    assert attestation["recipe_revision_id"]==spec.recipe_revision_id
    assert attestation["attempt_id"]==spec.attempt_id and attestation["generation"]==spec.generation
    assert attestation["candidate"]=={k:v for k,v in result.items() if k!="path"}
    assert attestation["stage_semantics"]==_stage_semantics()
    assert attestation["stage_semantics"]["id"]=="stage-engineering-v0"
    assert attestation["stage_semantics"]["neutral"] is False
    assert attestation["stage_semantics"]["extra_bookends"] is False
    assert attestation["stage_semantics"]["edge_fades"]=="stage-default-audio-video"
    assert attestation["stage_semantics"]["video_color"]=="stage-default-matching"
    assert candidate.path.is_relative_to(workspace)
    assert media.sha256(candidate.path)==candidate.sha256
    assert media.verify_inputs(bank)==original_manifest
    assert all(media.sha256(p)==h for p,h in staged_before.items())
    assert {p.name:media.sha256(p) for p in folder.iterdir()}==original_before
    assert core.jobs.get_job(owner,job.id).state==JobState.RUNNING
    assert build_core(config).media.get_recipe(owner,spec.recipe_revision_id).shots==spec.shots
    info=media.probe(result["path"])
    assert abs(float(info["format"]["duration"])-6)<.15
    media.decode(result["path"])
    # V2 engineering Stage speed=1, no manual effects; inherited fades/color remain.
    # Original JM oracle's 1.5x speed assertion
    # is intentionally replaced with speed=1 checkpoints, not weakened.
    frames=[]
    for at,color in [(1.5,"red"),(2.25,"blue"),(2.8,"blue"),(3.5,"yellow"),(4.5,"green")]:
        actual,rgb=media.frame_color(result["path"],at)
        assert actual==color,(at,actual,color)
        frames.append({"at":at,"color":actual,"rgb":rgb})
    tones=[]
    for at in (1,3,5):
        samples=samples_at(result["path"],at)
        values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}
        assert values[997]>=50 and values[997]>=10*max(values[223],values[443]),values
        tones.append({"at":at,"amplitudes":values})
    try:
        adapter.execute(spec)
    except EngineExecutionError:
        pass
    else:
        raise AssertionError("Attempt workspace reused")
    # Lost adapter response: recover the exact private manifest, then publish.
    # Verify the published MP4, not merely the renderer's temporary candidate.
    receipt=core.publication.recover(owner,ref)
    assert isinstance(receipt,ArtifactReceipt)
    assert receipt.path.parent==artifacts and receipt.path!=candidate.path
    assert receipt.sha256==candidate.sha256==media.sha256(receipt.path)
    assert receipt.snapshot_sha256==spec.snapshot_sha256
    assert receipt.stage_profile=="stage-engineering-v0"
    assert core.jobs.get_job(owner,job.id).state==JobState.SUCCEEDED
    assert core.jobs.get_task(owner,job.id).state==JobState.SUCCEEDED
    assert core.publication.publish(owner,ref,candidate)==receipt
    # Reopen from a genuinely separate isolated interpreter and package.
    reopen_code=(
        "import sys; from pathlib import Path; from montara_core.bootstrap.config import Config; "
        "from montara_core.bootstrap.core import build_core; "
        "from montara_core.domain.jobs import JobState; "
        "root=Path(sys.argv[1]); c=build_core(Config(sqlite_path=root/'core.db',"
        "media_root=root/'originals',workspace_root=root/'workspaces',artifact_root=root/'artifacts')); "
        "r=c.publication.get_artifact(sys.argv[2],sys.argv[3]); "
        "assert c.jobs.get_job(sys.argv[2],sys.argv[3]).state==JobState.SUCCEEDED; "
        "assert r.sha256==sys.argv[4]; print('PUBLISHED_REOPEN=PASS')"
    )
    assert "PUBLISHED_REOPEN=PASS" in subprocess.check_output(
        [sys.executable,"-I","-c",reopen_code,str(out),owner,job.id,receipt.sha256],
        cwd=out,text=True,timeout=30)
    media.decode(receipt.path)
    assert media.sha256(receipt.path)==candidate.sha256
    report={k:v for k,v in result.items() if k!="path"}
    report.update(status="PASS",scope="Core recipe to real Engine and durable published MP4",
                  duration_sec=float(info["format"]["duration"]),frames=frames,tones=tones,
                  resolution="1920x1080",fps=30,video_codec="h264",audio_codec="aac",
                  full_decode="PASS",originals_unchanged=True,workspace_reuse_rejected=True,
                  ffmpeg=original_manifest["ffmpeg"], canonical_execution_spec=True,
                  core_job_succeeded=True, publication="PASS", published_reopen="PASS", published_digest_verified=True,
                  preflight_negatives=negatives,
                  stage_semantics=attestation["stage_semantics"], candidate_manifest="PASS",
                  direct_staged_inputs="PASS", authored_silence={"status":"PASS","active_rms":active,"tail_rms":quiet},
                  authorized_bookend_padding={"status":"PASS","intro_rms":intro,"content_rms":content,"outro_rms":outro})
    (out/"quality-report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
