"""Contract mapping tests independent of Engine dependency installation."""
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from montara_core.adapters.zucker_engine import _plan, _copy_verified, EngineExecutionError
import tempfile
import hashlib


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

    def test_copy_verifies_full_hash_and_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            source=root/"source.mp4"
            source.write_bytes(b"original")
            item=NS(path=source,size_bytes=8,sha256=hashlib.sha256(b"original").hexdigest())
            _copy_verified(item,root/"copy")
            self.assertEqual((root/"copy").read_bytes(),b"original")
            source.write_bytes(b"modified")
            with self.assertRaises(EngineExecutionError): _copy_verified(item,root/"wrong")
            source.write_bytes(b"original")
            link=root/"link"
            link.symlink_to(source)
            item.path=link
            with self.assertRaises(EngineExecutionError): _copy_verified(item,root/"wrong")
