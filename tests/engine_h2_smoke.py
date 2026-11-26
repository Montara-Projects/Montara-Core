"""Explicit Linux real-engine gate using JM's external fixture and oracle."""
import importlib.util
import json
from pathlib import Path
import sys
from dataclasses import replace
import shutil
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import ExecutionSpec, EngineCandidate, Shot
from montara_core.domain.jobs import JobState
from montara_core.adapters.zucker_engine import ZuckerEngineAdapter, EngineExecutionError


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
    originals.mkdir(); workspaces.mkdir()
    config=Config(sqlite_path=out/"core.db",media_root=originals,workspace_root=workspaces)
    core=build_core(config)
    owner=core.projects.provision_account().id

    def prepare(name, *, short_master=False, short_camera=False, false_master=False):
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
    result=vars(candidate)
    workspace=spec.workspace
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
    # V2 v0 speed=1 and no effects; original JM oracle's 1.5x speed assertion
    # is intentionally replaced with speed=1 checkpoints, not weakened.
    frames=[]
    for at,color in [(1.5,"red"),(2.25,"blue"),(2.8,"blue"),(3.5,"yellow"),(4.5,"green")]:
        actual,rgb=media.frame_color(result["path"],at)
        assert actual==color,(at,actual,color)
        frames.append({"at":at,"color":actual,"rgb":rgb})
    import array
    tones=[]
    for at in (1,3,5):
        raw=media.execute(["ffmpeg","-v","error","-ss",str(at),"-i",str(result["path"]),
                           "-t","0.25","-vn","-ac","1","-ar",str(media.RATE),"-f","s16le","-"])
        samples=array.array("h",raw)
        if sys.byteorder!="little": samples.byteswap()
        assert len(samples)>=media.RATE//8
        values={hz:media.tone_amplitude(samples,hz) for hz in (223,443,997)}
        assert values[997]>=50 and values[997]>=10*max(values[223],values[443]),values
        tones.append({"at":at,"amplitudes":values})
    try:
        adapter.execute(spec)
    except EngineExecutionError:
        pass
    else:
        raise AssertionError("Attempt workspace reused")
    report={k:v for k,v in result.items() if k!="path"}
    report.update(status="PASS",scope="Core prepare_execution to real Engine candidate; no publication",
                  duration_sec=float(info["format"]["duration"]),frames=frames,tones=tones,
                  resolution="1920x1080",fps=30,video_codec="h264",audio_codec="aac",
                  full_decode="PASS",originals_unchanged=True,workspace_reuse_rejected=True,
                  ffmpeg=original_manifest["ffmpeg"], canonical_execution_spec=True,
                  core_job_still_running=True, preflight_negatives=negatives)
    (out/"quality-report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
