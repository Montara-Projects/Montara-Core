"""Core-owned local media/recipe metadata, authorized snapshot staging."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from uuid import uuid4

from montara_core.adapters.sqlite_db import SQLiteDatabase
from montara_core.adapters.local_media import LocalMedia
from montara_core.application.ports import StorageError
from montara_core.domain.execution import (
    ExecutionSpec, InputFile, OutputProfile, Shot, canonical_json, snapshot_document, snapshot_digest,
)
from montara_core.domain.jobs import AttemptRef, TransitionError
from montara_core.domain.media import (
    Asset, MediaLocation, MediaNotFound, RecipeRevision, Representation,
    supports_export_location, validate_recipe,
)
from montara_core.domain.projects import InvalidValue, ProjectNotFound
from montara_core.domain.pilot import FIXTURES, MAX_RECIPES, validate_pilot_recipe, validate_pilot_snapshot


class SQLiteMediaStore(SQLiteDatabase):
    _local: LocalMedia | None = None
    _pilot = None

    def _files(self) -> LocalMedia:
        if self._local is None:
            raise StorageError("local media roots not configured")
        return self._local

    @staticmethod
    def _owner(db, actor, project):
        if not db.execute("SELECT 1 FROM projects WHERE id=? AND owner_id=?", (project, actor)).fetchone():
            raise ProjectNotFound("project not found")

    @staticmethod
    def _representation(db, actor, representation):
        row = db.execute(
            "SELECT r.id,r.asset_id,r.revision,r.sha256,r.size_bytes,a.project_id,a.kind "
            "FROM representations r JOIN assets a ON a.id=r.asset_id "
            "JOIN projects p ON p.id=a.project_id WHERE r.id=? AND p.owner_id=?",
            (representation, actor),
        ).fetchone()
        if row is None:
            raise MediaNotFound("media not found")
        return Representation(*row[:5]), row[5], row[6]

    @staticmethod
    def _insert_rep(db, asset, revision, digest, size, locator):
        rep = Representation(str(uuid4()), asset, revision, digest, size)
        loc = MediaLocation(str(uuid4()), rep.id, locator)
        db.execute("INSERT INTO representations VALUES (?,?,?,?,?)",
                   (rep.id, asset, revision, digest, size))
        db.execute("INSERT INTO media_locations VALUES (?,?,?)",
                   (loc.id, rep.id, locator))
        return rep, loc

    def register(self, actor, project, kind, locator):
        if self._pilot is not None and FIXTURES.get(locator) != kind:
            raise InvalidValue("pilot permits only named fixture media")
        with self._connection() as db:
            self._owner(db, actor, project)
        digest, size = self._files().read(project, locator)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, actor, project)
            if self._pilot is not None:
                previous = db.execute(
                    "SELECT a.id,a.kind,r.id,r.revision,r.sha256,r.size_bytes,l.id "
                    "FROM assets a JOIN representations r ON r.asset_id=a.id "
                    "JOIN media_locations l ON l.representation_id=r.id "
                    "WHERE a.project_id=? AND l.locator=? ORDER BY r.revision DESC",
                    (project, locator),
                ).fetchall()
                if previous:
                    a, k, r, rev, h, n, loc = previous[0]
                    if len(previous) != 1 or (k,h,n) != (kind,digest,size):
                        raise TransitionError("pilot fixture changed; use an authorized new project")
                    return Asset(a,project,k), Representation(r,a,rev,h,n), MediaLocation(loc,r,locator)
                if db.execute("SELECT count(*) FROM assets WHERE project_id=?", (project,)).fetchone()[0] >= len(FIXTURES):
                    raise TransitionError("pilot media limit reached")
            asset = Asset(str(uuid4()), project, kind)
            db.execute("INSERT INTO assets VALUES (?,?,?)", (asset.id, project, kind))
            rep, loc = self._insert_rep(db, asset.id, 1, digest, size, locator)
            return asset, rep, loc

    def revise(self, actor, asset, expected, locator):
        with self._connection() as db:
            rep, project, _ = self._representation(db, actor, expected)
            if rep.asset_id != asset:
                raise MediaNotFound("media not found")
        digest, size = self._files().read(project, locator)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rep, _, _ = self._representation(db, actor, expected)
            latest = db.execute(
                "SELECT id FROM representations WHERE asset_id=? ORDER BY revision DESC LIMIT 1", (asset,)
            ).fetchone()
            if latest != (expected,):
                raise TransitionError("obsolete original revision")
            if (digest, size) == (rep.sha256, rep.size_bytes):
                raise TransitionError("identical content is a location, not a new revision")
            new, _ = self._insert_rep(db, asset, rep.revision + 1, digest, size, locator)
            return new

    def add_location(self, actor, representation, locator):
        with self._connection() as db:
            rep, project, _ = self._representation(db, actor, representation)
        if self._files().read(project, locator) != (rep.sha256, rep.size_bytes):
            raise StorageError("media location checksum mismatch")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._representation(db, actor, representation)
            existing = db.execute(
                "SELECT id FROM media_locations WHERE representation_id=? AND locator=?",
                (representation, locator),
            ).fetchone()
            if existing:
                return MediaLocation(existing[0], representation, locator)
            loc = MediaLocation(str(uuid4()), representation, locator)
            db.execute("INSERT INTO media_locations VALUES (?,?,?)", (loc.id, representation, locator))
            return loc

    def get_representation(self, actor, representation):
        with self._connection() as db:
            return self._representation(db, actor, representation)[0]

    @staticmethod
    def _recipe(row):
        return RecipeRevision(row[0], row[1], row[2], row[3], row[4],
                              tuple(Shot(**shot) for shot in json.loads(row[5])),
                              OutputProfile(**json.loads(row[6])))

    @staticmethod
    def _require_nominal_location(db, representation, kind):
        locations = db.execute(
            "SELECT locator FROM media_locations WHERE representation_id=?", (representation,)
        ).fetchall()
        if not any(supports_export_location(kind, row[0]) for row in locations):
            raise InvalidValue("H2 requires MP4/MOV camera and WAV master locations")

    @staticmethod
    def _validated_recipe_locations(db, recipe):
        """Validate stored snapshot without changing it or selecting newer revisions."""
        validate_recipe(recipe.master_representation_id, recipe.shots, recipe.profile)
        expected = {s.representation_id: "video" for s in recipe.shots}
        if recipe.master_representation_id in expected:
            raise InvalidValue("master cannot be a camera")
        expected[recipe.master_representation_id] = "audio"
        rows = db.execute(
            "SELECT r.id,r.sha256,r.size_bytes,l.locator,a.kind FROM recipe_inputs i "
            "JOIN representations r ON r.id=i.representation_id "
            "JOIN assets a ON a.id=r.asset_id "
            "JOIN media_locations l ON l.representation_id=r.id "
            "WHERE i.recipe_id=? AND a.project_id=? ORDER BY r.id,l.id",
            (recipe.id, recipe.project_id),
        ).fetchall()
        if ({row[0] for row in rows} != set(expected)
                or any(expected.get(row[0]) != row[4] for row in rows)):
            raise StorageError("missing authorized recipe inputs")
        compatible = [row[:4] for row in rows if supports_export_location(row[4], row[3])]
        if {row[0] for row in compatible} != set(expected):
            raise InvalidValue("H2 requires MP4/MOV camera and WAV master locations")
        return compatible

    def save_recipe(self, actor, project, expected, master, shots, profile):
        if self._pilot is not None:
            validate_pilot_recipe(shots)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._owner(db, actor, project)
            latest = db.execute(
                "SELECT id,revision FROM recipe_revisions WHERE project_id=? ORDER BY revision DESC LIMIT 1",
                (project,),
            ).fetchone()
            if expected != (latest[0] if latest else None):
                raise TransitionError("obsolete recipe revision")
            if self._pilot is not None and latest and latest[1] >= MAX_RECIPES:
                raise TransitionError("pilot recipe revision limit reached")
            inputs = {master: "audio"}
            for shot in shots:
                if shot.representation_id == master:
                    raise TransitionError("master cannot be a camera")
                inputs[shot.representation_id] = "video"
            for identity, expected_kind in inputs.items():
                rep, actual_project, kind = self._representation(db, actor, identity)
                if actual_project != project or kind != expected_kind:
                    raise MediaNotFound("media not found")
                if db.execute(
                    "SELECT 1 FROM representations WHERE asset_id=? AND revision>?",
                    (rep.asset_id, rep.revision),
                ).fetchone():
                    raise TransitionError("obsolete original revision")
                self._require_nominal_location(db, identity, kind)
            recipe = RecipeRevision(str(uuid4()), project, latest[1] + 1 if latest else 1,
                                    expected, master, shots, profile)
            db.execute("INSERT INTO recipe_revisions VALUES (?,?,?,?,?,?,?)",
                       (recipe.id, project, recipe.revision, expected, master,
                        json.dumps([asdict(s) for s in shots]), json.dumps(asdict(profile))))
            db.executemany("INSERT INTO recipe_inputs VALUES (?,?)",
                           [(recipe.id, identity) for identity in inputs])
            return recipe

    def get_recipe(self, actor, recipe):
        with self._connection() as db:
            row = db.execute(
                "SELECT r.* FROM recipe_revisions r JOIN projects p ON p.id=r.project_id "
                "WHERE r.id=? AND p.owner_id=?", (recipe, actor),
            ).fetchone()
            if row is None:
                raise MediaNotFound("recipe not found")
            return self._recipe(row)

    @staticmethod
    def _active_recipe(db, attempt):
        row = db.execute(
            "SELECT r.* FROM jobs j JOIN tasks t ON t.job_id=j.id "
            "JOIN attempts a ON a.task_id=t.id AND a.job_id=j.id "
            "JOIN recipe_revisions r ON r.id=t.recipe_id AND r.project_id=j.project_id "
            "WHERE j.id=? AND t.id=? AND a.id=? AND a.generation=? "
            "AND j.generation=a.generation AND j.state='running' AND t.state='running' "
            "AND a.state='running' AND t.kind='export'",
            (attempt.job_id, attempt.task_id, attempt.id, attempt.generation),
        ).fetchone()
        if row is None:
            raise TransitionError("inactive or invalid export attempt")
        return SQLiteMediaStore._recipe(row)

    @staticmethod
    def _snapshot_document(db, recipe):
        rows = db.execute(
            "SELECT r.id,r.sha256,r.size_bytes FROM recipe_inputs i "
            "JOIN representations r ON r.id=i.representation_id "
            "JOIN assets a ON a.id=r.asset_id WHERE i.recipe_id=? AND a.project_id=?",
            (recipe.id, recipe.project_id),
        ).fetchall()
        expected = {recipe.master_representation_id, *(s.representation_id for s in recipe.shots)}
        if {row[0] for row in rows} != expected:
            raise StorageError("missing snapshot representations")
        return snapshot_document(recipe.id, recipe.master_representation_id, recipe.shots,
                                 recipe.profile, [{"representation_id": r[0], "sha256": r[1],
                                                   "size_bytes": r[2]} for r in rows])

    @staticmethod
    def _snapshot(db, task_id, recipe):
        row = db.execute("SELECT document FROM export_snapshots WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise TransitionError("legacy export has no pinned execution snapshot; create a new job")
        document = SQLiteMediaStore._snapshot_document(db, recipe)
        if canonical_json(document) != row[0]:
            raise TransitionError("execution snapshot incompatible with current profile or inputs")
        return document

    def prepare_execution(self, attempt: AttemptRef) -> ExecutionSpec:
        local = self._files()
        with self._connection() as db:
            recipe = self._active_recipe(db, attempt)
            rows = self._validated_recipe_locations(db, recipe)
            document = self._snapshot(db, attempt.task_id, recipe)
            if self._pilot is not None:
                validate_pilot_snapshot(document)
        expected = {recipe.master_representation_id, *(s.representation_id for s in recipe.shots)}
        if {row[0] for row in rows} != expected:
            raise StorageError("missing authorized recipe inputs")
        workspace = local.workspace_root / attempt.id
        try:
            workspace.mkdir(mode=0o700)
        except OSError:
            raise StorageError("attempt workspace unavailable") from None
        staged = {}
        try:
            for identity, digest, size, locator in rows:
                if identity in staged:
                    continue
                # Retain the source's nominal suffix; only compatible locations
                # passed validation. Never rename unsupported formats as MP4/WAV.
                suffix = Path(locator).suffix
                target = workspace / (identity + suffix)
                try:
                    verified = local.read(recipe.project_id, locator, target)
                    if verified != (digest, size):
                        raise StorageError("media location checksum mismatch")
                except (StorageError, OSError):
                    target.unlink(missing_ok=True)
                    continue  # only another copy of this exact representation
                staged[identity] = InputFile(identity, target, digest, size)
            if set(staged) != expected:
                raise StorageError("no verified location for recipe input")
            with self._connection() as db:
                self._active_recipe(db, attempt)
            return ExecutionSpec(attempt.job_id, attempt.task_id, attempt.id,
                                 attempt.generation, recipe.id,
                                 tuple(staged[key] for key in sorted(staged)),
                                 recipe.master_representation_id, recipe.shots,
                                 recipe.profile, workspace, snapshot_digest(document))
        except BaseException:
            shutil.rmtree(workspace)
            raise

    def pilot_summary(self, actor, project):
        """Owner-scoped bounded read model; no filesystem locations returned."""
        if self._pilot is None:
            raise ProjectNotFound("project not found")
        with self._connection() as db:
            db.execute("BEGIN")
            self._owner(db, actor, project)
            media = db.execute(
                "SELECT r.id,a.kind,l.locator,r.size_bytes FROM representations r "
                "JOIN assets a ON a.id=r.asset_id JOIN media_locations l ON l.representation_id=r.id "
                "WHERE a.project_id=? ORDER BY r.revision DESC,r.id LIMIT 20", (project,)
            ).fetchall()
            latest = db.execute("SELECT * FROM recipe_revisions WHERE project_id=? ORDER BY revision DESC LIMIT 1",
                                (project,)).fetchone()
            jobs = db.execute("SELECT id,state,generation FROM jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 8",
                              (project,)).fetchall()
            return {"media": [{"id":r[0],"kind":r[1],"label":r[2],"size_bytes":r[3]}
                              for r in media if FIXTURES.get(r[2])==r[1]],
                    "recipe": asdict(self._recipe(latest)) if latest else None,
                    "jobs": [{"id":j[0],"state":j[1],"generation":j[2]} for j in jobs]}
