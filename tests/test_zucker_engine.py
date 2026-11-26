"""Contract mapping tests independent of Engine dependency installation."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from montara_core.adapters.zucker_engine import _plan, _verify_staged_input, _verify_manifest, EngineExecutionError
import tempfile
import hashlib
import json


def spec():
    return NS(profile=NS(width=1920,height=1080,fps=30,video_codec="h264",
                         audio_codec="aac",container="mp4"),
              inputs=(NS(representation_id="a"), NS(representation_id="b"),
                      NS(representation_id="master")),
              master_representation_id="master",
              shots=(NS(representation_id="a",start_ms=0,end_ms=2000),
                     NS(representation_id="b",start_ms=2000,end_ms=4000),
                     NS(representation_id="a",start_ms=4000,end_ms=6000)))


class EngineMappingTests(unittest.TestCase):
    def test_recipe_preserves_order_and_source_offsets(self):
        _, cameras, recipe, duration = _plan(spec())
        self.assertEqual(cameras, {"a":0,"b":1})
        self.assertEqual(duration, 6000)
        self.assertEqual([(s["source"],s["clip_start_sec"],s["master_start_sec"])
                          for s in recipe["segments"]], [(0,0,0),(1,2,2),(0,4,4)])
        self.assertTrue(all(s["playback_speed"] == 1 for s in recipe["segments"]))

    def test_wrong_profile_and_unknown_source_and_fractional_frames(self):
        for change in ("profile","source","time"):
            s=spec()
            if change=="profile": s.profile.width=640
            if change=="source": s.shots[0].representation_id="other"
            if change=="time": s.shots[0].end_ms=2001
            with self.subTest(change=change), self.assertRaises(EngineExecutionError):
                _plan(s)

    def test_capacity_bounds(self):
        for shots in [(NS(representation_id="a",start_ms=0,end_ms=21600100),),
                      tuple(NS(representation_id="a",start_ms=0,end_ms=100) for _ in range(1001)),
                      (NS(representation_id="a",start_ms=86400000,end_ms=86400100),)]:
            s=spec(); s.shots=shots
            with self.assertRaises(EngineExecutionError): _plan(s)

    def test_staged_input_verifies_full_hash_without_copy_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            source=root/"source.mp4"
            source.write_bytes(b"original")
            item=NS(path=source,size_bytes=8,sha256=hashlib.sha256(b"original").hexdigest())
            self.assertEqual(_verify_staged_input(item),source)
            self.assertEqual(list(root.iterdir()),[source])
            self.assertEqual(source.read_bytes(),b"original")
            source.write_bytes(b"modified")
            with self.assertRaises(EngineExecutionError): _verify_staged_input(item)
            source.write_bytes(b"original")
            link=root/"link"
            link.symlink_to(source)
            item.path=link
            with self.assertRaises(EngineExecutionError): _verify_staged_input(item)

    def test_manifest_rejects_changed_source_omission_or_proxy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            project=root/"work"/"edit.zuckervid"
            artifacts=project/"artifacts"; artifacts.mkdir(parents=True)
            effective=project/"effective.json"
            source=root/"a.mp4"; master=root/"master.wav"
            shot={"source":0,"clip_start_sec":0,"duration_sec":2,
                  "master_start_sec":0,"playback_speed":1}
            recipe={"segments":[shot]}
            plan={"inputs":{"master":{"path":str(master)}},"segments":[
                {**shot,"source_path":str(source),"clip_path":str(source)}]}
            report={"effective_plan_path":str(effective),"audio_source":"master",
                    "optional_omissions":[],"warnings":[],
                    "performance":{"segments":[{"rendered_from":"original","cached":False}]}}
            def write():
                effective.write_text(json.dumps(plan))
                (artifacts/"export_manifest.json").write_text(json.dumps(report))
            write()
            _verify_manifest(root,recipe,{"0":str(source)},master)
            for key in ("path","omission","proxy","cache","time","master","repair-warning"):
                with self.subTest(key=key):
                    old_plan=json.dumps(plan); old_report=json.dumps(report)
                    if key=="path": plan["segments"][0]["source_path"]="/foreign"
                    elif key=="omission": report["optional_omissions"]=[{"shot":1}]
                    elif key=="proxy": report["performance"]["segments"][0]["rendered_from"]="proxy"
                    elif key=="cache": report["performance"]["segments"][0]["cached"]=True
                    elif key=="time": plan["segments"][0]["clip_start_sec"]=1
                    elif key=="master": plan["inputs"]["master"]["path"]="/foreign"
                    else: report["warnings"]=["Original render failed; using proxy fallback"]
                    write()
                    with self.assertRaises(EngineExecutionError):
                        _verify_manifest(root,recipe,{"0":str(source)},master)
                    plan=json.loads(old_plan); report=json.loads(old_report)
