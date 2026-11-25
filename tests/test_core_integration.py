"""End-to-end internal development API from an installed package.

Child processes use isolated Python (-I), a temporary cwd/config/database,
and only trusted in-process service calls. No auth mock or HTTP actor API.
"""
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from montara_core.adapters.sqlite_core import SQLiteCoreStore
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config, load_config
from montara_core.bootstrap.core import build_core


_PREAMBLE = """
import json, sys
from pathlib import Path
from dataclasses import asdict
from montara_core.bootstrap.config import load_config
from montara_core.bootstrap.core import build_core
from montara_core.application.synthetic_worker import run_once
from montara_core.domain.jobs import AttemptRef, JobNotFound, JobState, TransitionError
from montara_core.domain.projects import ProjectNotFound
core = build_core(load_config(Path(sys.argv[1])))
data = json.loads(sys.argv[2])
"""

_CREATE = """
a = core.projects.provision_account()
b = core.projects.provision_account()
p = core.projects.create_project(a.id, "Synthetic A")
q = core.projects.create_project(b.id, "Synthetic B")
j = core.jobs.create_job(a.id, p.id, "fixture")
assert j.state == JobState.QUEUED
assert core.jobs.create_job(a.id, p.id, "fixture") == j
print(json.dumps({"owner": a.id, "other": b.id, "project": p.id,
                  "other_project": q.id, "job": j.id}))
"""


class CoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "core.toml"
        self.db = self.root / "core.sqlite"
        self.config.write_text(
            f'[http]\nport = 0\n[storage]\nsqlite_path = {json.dumps(str(self.db))}\n',
            encoding="utf-8",
        )

    def stage(self, code, data=None):
        result = subprocess.run(
            [sys.executable, "-I", "-c", _PREAMBLE + code,
             str(self.config), json.dumps(data or {})],
            cwd=self.root, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_one_composition_and_missing_storage(self):
        with patch.object(SQLiteCoreStore, "_initialize", autospec=True,
                          side_effect=SQLiteCoreStore._initialize) as initialize:
            core = build_core(load_config(self.config))
            self.assertEqual(initialize.call_count, 1)
        owner = core.projects.provision_account()
        project = core.projects.create_project(owner.id, "A")
        job = core.jobs.create_job(owner.id, project.id, "first")
        self.assertEqual(job.project_id, project.id)
        with self.assertRaises(StorageError):
            build_core(Config())

    def test_full_installed_flow_and_owner_isolation_after_reopen(self):
        ids = self.stage(_CREATE)
        result = self.stage("""
job = run_once(core.jobs)
assert job.id == data["job"] and job.state == JobState.SUCCEEDED
assert job.result == "synthetic:" + job.id
assert run_once(core.jobs) is None
print(json.dumps(asdict(job)))
""", ids)
        self.assertEqual(result["generation"], 1)
        self.assertEqual(result["state"], "succeeded")
        receipt = self.stage("""
owner, other, project, job = (data[k] for k in ("owner", "other", "project", "job"))
assert core.projects.get_project(owner, project).name == "Synthetic A"
assert [p.id for p in core.projects.list_projects(other)] == [data["other_project"]]
for action, error in (
    (lambda: core.projects.get_project(other, project), ProjectNotFound),
    (lambda: core.jobs.create_job(other, project, "forbidden"), ProjectNotFound),
    (lambda: core.jobs.get_job(other, job), JobNotFound),
    (lambda: core.jobs.cancel(other, job, 1), JobNotFound),
):
    try:
        action()
    except error:
        pass
    else:
        raise AssertionError("cross-owner access accepted")
stored = core.jobs.get_job(owner, job)
assert stored == core.jobs.create_job(owner, project, "fixture")
print(json.dumps(asdict(stored)))
""", ids)
        self.assertEqual(receipt, result)

    def test_lost_claim_recovery_and_stale_completion_across_processes(self):
        ids = self.stage(_CREATE)
        # Process exits after committing claim. Parent intentionally receives
        # no attempt identifier, simulating a lost response without a mock.
        self.stage("""
ref = core.jobs.claim()
assert ref.job_id == data["job"]
print(json.dumps({"claim_committed": True}))
""", ids)
        recovered = self.stage("""
old = core.jobs.running_attempt()
assert old.job_id == data["job"] and old.generation == 1
assert core.jobs.claim() is None
assert core.jobs.recover(old).state == JobState.FAILED
assert core.jobs.retry(data["owner"], old).state == JobState.QUEUED
print(json.dumps(asdict(old)))
""", ids)
        done = self.stage("""
old = AttemptRef(**data["old"])
new = core.jobs.claim()
assert new.generation == 2 and new.id != old.id
try:
    core.jobs.succeed(old, "obsolete")
except TransitionError:
    pass
else:
    raise AssertionError("stale result accepted")
assert core.jobs.get_job(data["owner"], new.job_id).result is None
done = core.jobs.succeed(new, "synthetic:" + new.job_id)
print(json.dumps(asdict(done)))
""", {**ids, "old": recovered})
        stored = self.stage("""
old = AttemptRef(**data["old"])
job = core.jobs.get_job(data["owner"], data["job"])
assert core.jobs.running_attempt() is None
try:
    core.jobs.succeed(old, "obsolete")
except TransitionError:
    pass
else:
    raise AssertionError("stale result accepted after reopen")
assert core.jobs.succeed(AttemptRef(job.id, job.attempt_id, job.generation), job.result) == job
assert core.jobs.create_job(data["owner"], data["project"], "fixture") == job
assert run_once(core.jobs) is None
print(json.dumps(asdict(job)))
""", {**ids, "old": recovered})
        self.assertEqual(stored, done)
        self.assertEqual(stored["generation"], 2)
        self.assertEqual(stored["result"], "synthetic:" + ids["job"])

    def test_configured_http_health_has_no_actor_operations(self):
        ids = self.stage(_CREATE)
        with subprocess.Popen(
            [sys.executable, "-I", "-m", "montara_core", "--config", str(self.config)],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) as process:
            lines = queue.Queue()
            reader = threading.Thread(
                target=lambda: lines.put(process.stdout.readline()), daemon=True
            )
            reader.start()
            try:
                match = re.fullmatch(
                    r"Montara Core health listening on 127\.0\.0\.1:(\d+)\n",
                    lines.get(timeout=10),
                )
                self.assertIsNotNone(match)
                url = f"http://127.0.0.1:{match.group(1)}"
                with urlopen(url + "/health", timeout=5) as response:
                    self.assertEqual(json.load(response), {"status": "ok"})
                for path, method, code in (
                    ("/projects", "GET", 404),
                    ("/projects/" + ids["project"], "GET", 404),
                    ("/jobs/" + ids["job"], "GET", 404),
                    ("/jobs", "POST", 501),
                ):
                    request = Request(url + path, method=method,
                                      headers={"X-Actor": ids["owner"]},
                                      data=b"{}" if method == "POST" else None)
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(request, timeout=5)
                    with caught.exception as response:
                        self.assertEqual(response.code, code)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                reader.join(timeout=2)
        current = build_core(load_config(self.config))
        self.assertEqual(current.jobs.get_job(ids["owner"], ids["job"]).state, "queued")
        self.assertEqual(len(current.projects.list_projects(ids["owner"])), 1)
