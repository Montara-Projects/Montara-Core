"""Prepare an append-only admission change; no DB, services or network."""
import argparse
import copy
import importlib.util
import os
import tomllib
from pathlib import Path

spec = importlib.util.spec_from_file_location("render_config", Path(__file__).with_name("render-config.py"))
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

def extend(d, current, candidate):
    old_core, _, _ = render.generate(d)
    old = tomllib.loads(old_core)
    if old != current:
        raise ValueError("current configuration differs from declarative source")
    proposed = copy.deepcopy(d)
    proposed["identity"]["allowed_subjects_file"] = str(candidate)
    new_core, _, _ = render.generate(proposed)
    new = tomllib.loads(new_core)
    before = old["pilot"]["allowed_subjects"]
    after = new["pilot"]["allowed_subjects"]
    if not 1 <= len(after)-len(before) <= 2 or after[:len(before)] != before:
        raise ValueError("only append one or two distinct verified subjects")
    comparison = copy.deepcopy(new)
    comparison["pilot"]["allowed_subjects"] = before
    if comparison != current:
        raise ValueError("unrelated configuration change")
    return new_core

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("operator", type=Path)
    ap.add_argument("current", type=Path)
    ap.add_argument("candidate", type=Path)
    ap.add_argument("output", type=Path)
    a = ap.parse_args()
    os.umask(0o077)
    d = tomllib.loads(render.private_text(a.operator))
    current = tomllib.loads(a.current.read_text(encoding="utf-8"))
    new = extend(d, current, a.candidate)
    with a.output.open("x", encoding="utf-8") as f:
        f.write(new+"\n")
    print("APPEND_ONLY_CONFIG=PASS")

if __name__ == "__main__":
    main()
