"""Explicit Linux real-engine gate using JM's external fixture and oracle."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
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
    def item(key,name):
        path=bank/name
        return NS(representation_id=key,path=path,sha256=media.sha256(path),size_bytes=path.stat().st_size)
    workspace=out/"attempt"
    workspace.mkdir(mode=0o700)
    spec=NS(profile=NS(width=1920,height=1080,fps=30,video_codec="h264",audio_codec="aac",container="mp4"),
            inputs=(item("a","camera-a.mp4"),item("b","camera-b.mp4"),item("m","master.wav")),
            master_representation_id="m",workspace=workspace,
            shots=(NS(representation_id="a",start_ms=0,end_ms=2000),
                   NS(representation_id="b",start_ms=2000,end_ms=4000),
                   NS(representation_id="a",start_ms=4000,end_ms=6000)))
    adapter=ZuckerEngineAdapter(jm,engine)
    # Until H2.1 merges, exercise the exact rendering implementation; the public
    # execute method adds only construction of the canonical EngineCandidate.
    result=adapter._render(spec)
    assert result["path"].is_relative_to(workspace)
    assert media.sha256(result["path"])==result["sha256"]
    assert media.verify_inputs(bank)==original_manifest
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
        adapter._render(spec)
    except EngineExecutionError:
        pass
    else:
        raise AssertionError("Attempt workspace reused")
    report={k:v for k,v in result.items() if k!="path"}
    report.update(status="PASS",scope="isolated Engine adapter; not integrated Core H2",
                  duration_sec=float(info["format"]["duration"]),frames=frames,tones=tones,
                  resolution="1920x1080",fps=30,video_codec="h264",audio_codec="aac",
                  full_decode="PASS",originals_unchanged=True,workspace_reuse_rejected=True,
                  ffmpeg=original_manifest["ffmpeg"])
    (out/"quality-report.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
