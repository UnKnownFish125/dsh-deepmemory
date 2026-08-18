"""deepmemory v2 data contracts and lifecycle primitives.

This module deliberately depends only on Python's standard library. The
existing server owns HTTP, vector search, graph, backup, and archive behavior;
this module owns the P1 transactional data model that later API work can call.
"""

import hashlib
import json
import sqlite3
import time
import uuid


V2_SCHEMA_VERSION = 3

TASK_STATUSES = ("planned", "todo", "in_progress", "completed", "failed")
TASK_COLORS = ("neutral", "red", "orange", "yellow", "green", "blue")
CARD_KINDS = ("task", "daily")
CARD_ACTORS = ("user", "main_agent", "system")
MEMORY_ACTORS = ("user", "main_agent", "model", "system")
MEMORY_CLASSES = (
    "semantic",
    "short_term",
    "process",
    "source_archive",
    "compressed_archive",
)
STORAGE_TIERS = ("active", "cold", "archive")
DECISION_STATUSES = (
    "none",
    "proposed",
    "exploring",
    "pending",
    "adopted",
    "rejected",
    "superseded",
    "invalid",
)
SENSITIVITY_LEVELS = ("normal", "sensitive", "protected", "secret")


class DomainError(Exception):
    """Base class for v2 domain contract violations."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class PermissionDenied(DomainError):
    pass


class InvalidTransition(DomainError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_columns(conn, table, columns):
    present = _columns(conn, table)
    for name, definition in columns:
        if name not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            present.add(name)


V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  parent_task_id TEXT REFERENCES tasks(id),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  task_color TEXT NOT NULL DEFAULT 'neutral',
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK(status IN ('planned','todo','in_progress','completed','failed')),
  blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)),
  block_reason TEXT NOT NULL DEFAULT '',
  missing_conditions TEXT NOT NULL DEFAULT '[]',
  completion_criteria TEXT NOT NULL DEFAULT '',
  failure_reason TEXT NOT NULL DEFAULT '',
  failure_evidence TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  source_message_id TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  CHECK(blocked = 0 OR status = 'in_progress')
);

CREATE TABLE IF NOT EXISTS task_events (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  event_type TEXT NOT NULL
    CHECK(event_type IN ('created','status_changed','blocked','unblocked','failed','reopened')),
  from_status TEXT,
  to_status TEXT,
  reason TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL CHECK(attempt >= 1),
  actor TEXT NOT NULL DEFAULT 'main_agent'
    CHECK(actor IN ('user','main_agent','system')),
  source_message_id TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS state_cards (
  id TEXT PRIMARY KEY,
  card_key TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('task','daily')),
  task_id TEXT REFERENCES tasks(id),
  payload TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  current_revision_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(card_key, kind),
  CHECK(kind != 'task' OR task_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS state_card_revisions (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL REFERENCES state_cards(id),
  parent_revision_id TEXT REFERENCES state_card_revisions(id),
  version INTEGER NOT NULL CHECK(version >= 1),
  patch TEXT NOT NULL DEFAULT '{}',
  before_payload TEXT NOT NULL DEFAULT '{}',
  after_payload TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL CHECK(actor IN ('user','main_agent','system')),
  reason TEXT NOT NULL DEFAULT '',
  source_message_id TEXT NOT NULL DEFAULT '',
  tool_trace_id TEXT NOT NULL DEFAULT '',
  subagent_trace_id TEXT NOT NULL DEFAULT '',
  before_hash TEXT NOT NULL,
  after_hash TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(card_id, version)
);

CREATE TABLE IF NOT EXISTS memory_revisions (
  id TEXT PRIMARY KEY,
  memory_id INTEGER NOT NULL REFERENCES documents(id),
  parent_revision_id TEXT REFERENCES memory_revisions(id),
  version INTEGER NOT NULL CHECK(version >= 1),
  patch TEXT NOT NULL,
  before_payload TEXT NOT NULL,
  after_payload TEXT NOT NULL,
  actor TEXT NOT NULL CHECK(actor IN ('user','main_agent','model','system')),
  reason TEXT NOT NULL DEFAULT '',
  source_message_id TEXT NOT NULL DEFAULT '',
  trace_id TEXT NOT NULL DEFAULT '',
  before_hash TEXT NOT NULL,
  after_hash TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(memory_id, version)
);

CREATE TABLE IF NOT EXISTS memory_archives (
  id TEXT PRIMARY KEY,
  memory_id INTEGER REFERENCES documents(id),
  archive_kind TEXT NOT NULL CHECK(archive_kind IN ('cold','compressed')),
  summary TEXT NOT NULL DEFAULT '',
  source_refs TEXT NOT NULL DEFAULT '[]',
  period_start REAL,
  period_end REAL,
  created_at REAL NOT NULL,
  CHECK(period_end IS NULL OR period_start IS NULL OR period_end >= period_start)
);

CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, blocked);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_cards_task ON state_cards(task_id);
CREATE INDEX IF NOT EXISTS idx_card_revisions_card ON state_card_revisions(card_id, version);
CREATE INDEX IF NOT EXISTS idx_memory_revisions_memory ON memory_revisions(memory_id, version);
CREATE INDEX IF NOT EXISTS idx_memory_archives_memory ON memory_archives(memory_id);

CREATE TRIGGER IF NOT EXISTS task_events_immutable_update
BEFORE UPDATE ON task_events BEGIN
  SELECT RAISE(ABORT, 'task events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS task_events_immutable_delete
BEFORE DELETE ON task_events BEGIN
  SELECT RAISE(ABORT, 'task events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS card_revisions_immutable_update
BEFORE UPDATE ON state_card_revisions BEGIN
  SELECT RAISE(ABORT, 'state card revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS card_revisions_immutable_delete
BEFORE DELETE ON state_card_revisions BEGIN
  SELECT RAISE(ABORT, 'state card revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS memory_revisions_immutable_update
BEFORE UPDATE ON memory_revisions BEGIN
  SELECT RAISE(ABORT, 'memory revisions are immutable');
END;
CREATE TRIGGER IF NOT EXISTS memory_revisions_immutable_delete
BEFORE DELETE ON memory_revisions BEGIN
  SELECT RAISE(ABORT, 'memory revisions are immutable');
END;
"""


DOCUMENT_COLUMNS = (
    ("memory_class", "TEXT NOT NULL DEFAULT 'semantic'"),
    ("storage_tier", "TEXT NOT NULL DEFAULT 'active'"),
    ("decision_status", "TEXT NOT NULL DEFAULT 'none'"),
    ("disputed", "INTEGER NOT NULL DEFAULT 0"),
    ("supersedes_id", "INTEGER REFERENCES documents(id)"),
    ("event_time_start", "REAL"),
    ("event_time_end", "REAL"),
    ("time_raw", "TEXT NOT NULL DEFAULT ''"),
    ("time_precision", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("time_confidence", "REAL NOT NULL DEFAULT 0"),
    ("time_inferred", "INTEGER NOT NULL DEFAULT 0"),
    ("sensitivity_level", "TEXT NOT NULL DEFAULT 'normal'"),
    ("confirmation_status", "TEXT NOT NULL DEFAULT 'unconfirmed'"),
    ("source_quality", "REAL NOT NULL DEFAULT 0.5"),
    ("actionability", "REAL NOT NULL DEFAULT 0"),
    ("reject_penalty", "REAL NOT NULL DEFAULT 0"),
    ("lifecycle_expires_at", "REAL"),
    ("cold_at", "REAL"),
    ("compressed_at", "REAL"),
    ("task_id", "TEXT REFERENCES tasks(id)"),
    ("source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("source_message_id", "TEXT NOT NULL DEFAULT ''"),
    ("trace_id", "TEXT NOT NULL DEFAULT ''"),
    ("revision_version", "INTEGER NOT NULL DEFAULT 0"),
    ("updated_at", "REAL"),
)

SOURCE_COLUMNS = (
    ("source_type", "TEXT NOT NULL DEFAULT 'message'"),
    ("source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("trace_id", "TEXT NOT NULL DEFAULT ''"),
    ("task_id", "TEXT REFERENCES tasks(id)"),
    ("sensitivity_level", "TEXT NOT NULL DEFAULT 'normal'"),
)


VALIDATION_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS documents_v2_validate_insert
BEFORE INSERT ON documents BEGIN
  SELECT CASE WHEN NEW.memory_class NOT IN ('semantic','short_term','process','source_archive','compressed_archive')
    THEN RAISE(ABORT, 'invalid memory_class') END;
  SELECT CASE WHEN NEW.storage_tier NOT IN ('active','cold','archive')
    THEN RAISE(ABORT, 'invalid storage_tier') END;
  SELECT CASE WHEN NEW.decision_status NOT IN ('none','proposed','exploring','pending','adopted','rejected','superseded','invalid')
    THEN RAISE(ABORT, 'invalid decision_status') END;
  SELECT CASE WHEN NEW.sensitivity_level NOT IN ('normal','sensitive','protected','secret')
    THEN RAISE(ABORT, 'invalid sensitivity_level') END;
  SELECT CASE WHEN NEW.disputed NOT IN (0,1) OR NEW.time_inferred NOT IN (0,1)
    THEN RAISE(ABORT, 'invalid boolean field') END;
  SELECT CASE WHEN NEW.time_confidence < 0 OR NEW.time_confidence > 1
    THEN RAISE(ABORT, 'invalid time_confidence') END;
  SELECT CASE WHEN NEW.event_time_end IS NOT NULL AND NEW.event_time_start IS NOT NULL
    AND NEW.event_time_end < NEW.event_time_start
    THEN RAISE(ABORT, 'invalid event time range') END;
END;
CREATE TRIGGER IF NOT EXISTS documents_v2_validate_update
BEFORE UPDATE ON documents BEGIN
  SELECT CASE WHEN NEW.memory_class NOT IN ('semantic','short_term','process','source_archive','compressed_archive')
    THEN RAISE(ABORT, 'invalid memory_class') END;
  SELECT CASE WHEN NEW.storage_tier NOT IN ('active','cold','archive')
    THEN RAISE(ABORT, 'invalid storage_tier') END;
  SELECT CASE WHEN NEW.decision_status NOT IN ('none','proposed','exploring','pending','adopted','rejected','superseded','invalid')
    THEN RAISE(ABORT, 'invalid decision_status') END;
  SELECT CASE WHEN NEW.sensitivity_level NOT IN ('normal','sensitive','protected','secret')
    THEN RAISE(ABORT, 'invalid sensitivity_level') END;
  SELECT CASE WHEN NEW.disputed NOT IN (0,1) OR NEW.time_inferred NOT IN (0,1)
    THEN RAISE(ABORT, 'invalid boolean field') END;
  SELECT CASE WHEN NEW.time_confidence < 0 OR NEW.time_confidence > 1
    THEN RAISE(ABORT, 'invalid time_confidence') END;
  SELECT CASE WHEN NEW.event_time_end IS NOT NULL AND NEW.event_time_start IS NOT NULL
    AND NEW.event_time_end < NEW.event_time_start
    THEN RAISE(ABORT, 'invalid event time range') END;
END;
"""


def install_v2_schema(conn):
    """Install or repair the P1 schema without replacing legacy objects."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(V2_SCHEMA)
    if "documents" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "documents", DOCUMENT_COLUMNS)
        conn.executescript(VALIDATION_TRIGGERS)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_v2_lifecycle "
            "ON documents(storage_tier, memory_class, decision_status, disputed)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_v2_task ON documents(task_id)"
        )
    if "sources" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "sources", SOURCE_COLUMNS)
    if "tasks" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "tasks", (("task_color", "TEXT NOT NULL DEFAULT 'neutral'"),))


class V2Store:
    """Transactional task, card, and memory lifecycle operations."""

    def __init__(self, db_path):
        self.db_path = db_path

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def migrate(self):
        with self._connect() as conn:
            install_v2_schema(conn)

    def create_task(self, title, status="planned", parent_task_id=None, **fields):
        if status not in TASK_STATUSES:
            raise InvalidTransition(f"unknown task status: {status}")
        task_id = fields.pop("task_id", None) or str(uuid.uuid4())
        now = time.time()
        blocked = bool(fields.get("blocked", False))
        task_color = fields.get("task_color", "neutral") or "neutral"
        if task_color not in TASK_COLORS:
            raise InvalidTransition(f"unknown task color: {task_color}")
        if blocked and status != "in_progress":
            raise InvalidTransition("only in_progress tasks may be blocked")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id,parent_task_id,title,description,task_color,status,blocked,"
                "block_reason,missing_conditions,completion_criteria,source_message_id,"
                "trace_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    parent_task_id,
                    title,
                    fields.get("description", ""),
                    task_color,
                    status,
                    int(blocked),
                    fields.get("block_reason", ""),
                    _json(fields.get("missing_conditions", [])),
                    fields.get("completion_criteria", ""),
                    fields.get("source_message_id", ""),
                    fields.get("trace_id", ""),
                    now,
                    now,
                ),
            )
            self._task_event(conn, task_id, "created", None, status, 1, fields)
        return self.get_task(task_id)

    def get_task(self, task_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"task not found: {task_id}")
        result = dict(row)
        result["missing_conditions"] = json.loads(result["missing_conditions"] or "[]")
        return result

    def transition_task(
        self,
        task_id,
        to_status,
        expected_version,
        reason="",
        evidence="",
        actor="main_agent",
        source_message_id="",
        trace_id="",
    ):
        allowed = {
            "planned": {"todo"},
            "todo": {"in_progress"},
            "in_progress": {"completed", "failed"},
            "failed": {"todo", "in_progress"},
            "completed": set(),
        }
        if to_status not in TASK_STATUSES:
            raise InvalidTransition(f"unknown task status: {to_status}")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["version"] != expected_version:
                raise ConflictError(
                    f"task version conflict: expected {expected_version}, current {row['version']}"
                )
            if to_status not in allowed[row["status"]]:
                raise InvalidTransition(f"cannot move {row['status']} to {to_status}")
            if to_status in ("completed", "failed") and row["blocked"]:
                raise InvalidTransition("unblock a task before closing its attempt")
            if to_status == "failed" and not reason:
                raise InvalidTransition("failed tasks require a reason")
            reopened = row["status"] == "failed"
            attempt = row["attempt"] + (1 if reopened else 0)
            failure_reason = reason if to_status == "failed" else row["failure_reason"]
            failure_evidence = evidence if to_status == "failed" else row["failure_evidence"]
            conn.execute(
                "UPDATE tasks SET status=?,attempt=?,failure_reason=?,failure_evidence=?,"
                "version=version+1,updated_at=? WHERE id=?",
                (to_status, attempt, failure_reason, failure_evidence, time.time(), task_id),
            )
            event_type = "reopened" if reopened else ("failed" if to_status == "failed" else "status_changed")
            self._task_event(
                conn,
                task_id,
                event_type,
                row["status"],
                to_status,
                attempt,
                {
                    "reason": reason,
                    "evidence": evidence,
                    "actor": actor,
                    "source_message_id": source_message_id,
                    "trace_id": trace_id,
                },
            )
        return self.get_task(task_id)

    def set_task_blocked(
        self,
        task_id,
        blocked,
        expected_version,
        reason="",
        missing_conditions=None,
        actor="main_agent",
    ):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["version"] != expected_version:
                raise ConflictError("task version conflict")
            if blocked and row["status"] != "in_progress":
                raise InvalidTransition("only in_progress tasks may be blocked")
            conn.execute(
                "UPDATE tasks SET blocked=?,block_reason=?,missing_conditions=?,"
                "version=version+1,updated_at=? WHERE id=?",
                (
                    int(bool(blocked)),
                    reason if blocked else "",
                    _json(missing_conditions or []),
                    time.time(),
                    task_id,
                ),
            )
            self._task_event(
                conn,
                task_id,
                "blocked" if blocked else "unblocked",
                row["status"],
                row["status"],
                row["attempt"],
                {"reason": reason, "actor": actor},
            )
        return self.get_task(task_id)

    def set_task_color(self, task_id, task_color, expected_version):
        if task_color not in TASK_COLORS:
            raise InvalidTransition(f"unknown task color: {task_color}")
        with self._connect() as conn:
            row = conn.execute("SELECT version FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["version"] != expected_version:
                raise ConflictError("task version conflict")
            conn.execute(
                "UPDATE tasks SET task_color=?,version=version+1,updated_at=? WHERE id=?",
                (task_color, time.time(), task_id),
            )
        return self.get_task(task_id)

    @staticmethod
    def _task_event(conn, task_id, event_type, from_status, to_status, attempt, fields):
        actor = fields.get("actor", "main_agent")
        if actor not in CARD_ACTORS:
            raise PermissionDenied("sub-agent/model paths cannot mutate the main task board")
        conn.execute(
            "INSERT INTO task_events (id,task_id,event_type,from_status,to_status,reason,"
            "evidence,attempt,actor,source_message_id,trace_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                from_status,
                to_status,
                fields.get("reason", ""),
                fields.get("evidence", ""),
                attempt,
                actor,
                fields.get("source_message_id", ""),
                fields.get("trace_id", ""),
                time.time(),
            ),
        )

    def task_history(self, task_id):
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
            if exists is None:
                raise NotFoundError(f"task not found: {task_id}")
            rows = conn.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY created_at,id", (task_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tasks(self, status=None, parent_task_id=None, limit=100):
        """Return a bounded task-board view without exposing raw sqlite rows."""
        clauses, args = [], []
        if status:
            if status not in TASK_STATUSES:
                raise DomainError(f"unknown task status: {status}")
            clauses.append("status=?")
            args.append(status)
        if parent_task_id is not None:
            clauses.append("parent_task_id=?")
            args.append(parent_task_id)
        limit = max(1, min(int(limit), 500))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ?",
                args + [limit],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["missing_conditions"] = json.loads(item["missing_conditions"] or "[]")
            result.append(item)
        return result

    def put_state_card(
        self,
        card_key,
        kind,
        payload,
        expected_version=None,
        task_id=None,
        actor="main_agent",
        reason="",
        source_message_id="",
        tool_trace_id="",
        subagent_trace_id="",
    ):
        if kind not in CARD_KINDS:
            raise DomainError(f"unknown card kind: {kind}")
        if actor not in CARD_ACTORS:
            raise PermissionDenied("sub-agent/model paths cannot mutate state cards")
        if kind == "task" and not task_id:
            raise DomainError("task cards require task_id")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM state_cards WHERE card_key=? AND kind=?", (card_key, kind)
            ).fetchone()
            before = json.loads(row["payload"]) if row else {}
            if row:
                if expected_version is None or row["version"] != expected_version:
                    raise ConflictError(
                        f"card version conflict: expected {expected_version}, current {row['version']}"
                    )
                card_id = row["id"]
                if kind == "task" and task_id is None:
                    task_id = row["task_id"]
                version = row["version"] + 1
                parent_revision_id = row["current_revision_id"]
            else:
                if expected_version not in (None, 0):
                    raise ConflictError("card does not exist")
                card_id = str(uuid.uuid4())
                version = 1
                parent_revision_id = None
                conn.execute(
                    "INSERT INTO state_cards (id,card_key,kind,task_id,payload,version,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (card_id, card_key, kind, task_id, _json(payload), version, now, now),
                )
            patch = {
                key: {"before": before.get(key), "after": payload.get(key)}
                for key in sorted(set(before) | set(payload))
                if before.get(key) != payload.get(key)
            }
            revision_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO state_card_revisions (id,card_id,parent_revision_id,version,"
                "patch,before_payload,after_payload,actor,reason,source_message_id,"
                "tool_trace_id,subagent_trace_id,before_hash,after_hash,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    card_id,
                    parent_revision_id,
                    version,
                    _json(patch),
                    _json(before),
                    _json(payload),
                    actor,
                    reason,
                    source_message_id,
                    tool_trace_id,
                    subagent_trace_id,
                    _hash_payload(before),
                    _hash_payload(payload),
                    now,
                ),
            )
            conn.execute(
                "UPDATE state_cards SET task_id=?,payload=?,version=?,current_revision_id=?,"
                "updated_at=? WHERE id=?",
                (task_id, _json(payload), version, revision_id, now, card_id),
            )
        return self.get_state_card(card_key, kind)

    def get_state_card(self, card_key, kind):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM state_cards WHERE card_key=? AND kind=?", (card_key, kind)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"state card not found: {kind}/{card_key}")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def state_card_revisions(self, card_key, kind, limit=100):
        with self._connect() as conn:
            card = conn.execute(
                "SELECT 1 FROM state_cards WHERE card_key=? AND kind=?", (card_key, kind)
            ).fetchone()
            if card is None:
                raise NotFoundError(f"state card not found: {kind}/{card_key}")
            rows = conn.execute(
                "SELECT r.* FROM state_card_revisions r "
                "JOIN state_cards c ON c.id=r.card_id "
                "WHERE c.card_key=? AND c.kind=? ORDER BY r.version DESC LIMIT ?",
                (card_key, kind, max(1, min(int(limit), 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("patch", "before_payload", "after_payload"):
                item[key] = json.loads(item[key] or "{}")
            result.append(item)
        return result

    def restore_state_card(self, card_key, kind, revision_id, expected_version, **audit):
        with self._connect() as conn:
            revision = conn.execute(
                "SELECT r.after_payload FROM state_card_revisions r "
                "JOIN state_cards c ON c.id=r.card_id "
                "WHERE r.id=? AND c.card_key=? AND c.kind=?",
                (revision_id, card_key, kind),
            ).fetchone()
            card = conn.execute(
                "SELECT task_id FROM state_cards WHERE card_key=? AND kind=?", (card_key, kind)
            ).fetchone()
        if revision is None or card is None:
            raise NotFoundError("state card revision not found")
        audit.setdefault("reason", f"restore revision {revision_id}")
        return self.put_state_card(
            card_key,
            kind,
            json.loads(revision["after_payload"]),
            expected_version=expected_version,
            task_id=card["task_id"],
            **audit,
        )

    def update_memory_lifecycle(
        self,
        memory_id,
        changes,
        expected_version,
        actor="model",
        reason="",
        source_message_id="",
        trace_id="",
        explicit_execution=False,
    ):
        allowed = {
            "content",
            "key_facts",
            "persona_summary",
            "canonical_summary",
            "type",
            "domain",
            "scope",
            "workspace_id",
            "session_id",
            "persona_id",
            "importance",
            "memory_class",
            "storage_tier",
            "decision_status",
            "disputed",
            "supersedes_id",
            "event_time_start",
            "event_time_end",
            "time_raw",
            "time_precision",
            "time_confidence",
            "time_inferred",
            "sensitivity_level",
            "confirmation_status",
            "source_quality",
            "actionability",
            "reject_penalty",
            "lifecycle_expires_at",
            "cold_at",
            "compressed_at",
            "task_id",
            "source_ref",
            "source_message_id",
            "trace_id",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise DomainError(f"unsupported memory fields: {sorted(unknown)}")
        if not changes:
            raise DomainError("memory lifecycle update requires at least one field")
        if actor not in MEMORY_ACTORS:
            raise PermissionDenied(f"unknown memory actor: {actor}")
        decision = changes.get("decision_status")
        if decision == "invalid" and actor != "user":
            raise PermissionDenied("only an explicit user action may set invalid")
        if decision == "adopted" and actor == "model" and not explicit_execution:
            raise PermissionDenied("candidate discussion cannot be promoted to adopted")
        if decision is not None and decision not in DECISION_STATUSES:
            raise DomainError(f"unknown decision status: {decision}")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"memory not found: {memory_id}")
            if row["revision_version"] != expected_version:
                raise ConflictError(
                    f"memory version conflict: expected {expected_version}, current {row['revision_version']}"
                )
            # Test fixtures and pre-v2 installations may have a minimal legacy
            # documents table; only include columns that actually exist in the
            # row while preserving the full revision payload on new schemas.
            before = {key: row[key] for key in allowed if key in row.keys()}
            after = dict(before)
            after.update(changes)
            version = row["revision_version"] + 1
            parent = conn.execute(
                "SELECT id FROM memory_revisions WHERE memory_id=? ORDER BY version DESC LIMIT 1",
                (memory_id,),
            ).fetchone()
            fields = sorted(changes)
            assignments = ",".join(f"{key}=?" for key in fields)
            conn.execute(
                f"UPDATE documents SET {assignments},revision_version=?,updated_at=? WHERE id=?",
                [changes[key] for key in fields] + [version, time.time(), memory_id],
            )
            conn.execute(
                "INSERT INTO memory_revisions (id,memory_id,parent_revision_id,version,patch,"
                "before_payload,after_payload,actor,reason,source_message_id,trace_id,before_hash,"
                "after_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    memory_id,
                    parent["id"] if parent else None,
                    version,
                    _json(changes),
                    _json(before),
                    _json(after),
                    actor,
                    reason,
                    source_message_id or changes.get("source_message_id", ""),
                    trace_id or changes.get("trace_id", ""),
                    _hash_payload(before),
                    _hash_payload(after),
                    time.time(),
                ),
            )
        return version

    def get_memory(self, memory_id):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (int(memory_id),)).fetchone()
        if row is None:
            raise NotFoundError(f"memory not found: {memory_id}")
        return dict(row)

    def migrate_memory(self, memory_id, target_tier, expected_version, actor="main_agent", reason=""):
        """Perform one explicit lifecycle hop; callers cannot skip a tier."""
        row = self.get_memory(memory_id)
        current = row["storage_tier"]
        if target_tier not in STORAGE_TIERS:
            raise DomainError(f"unknown storage tier: {target_tier}")
        valid = {"active": {"cold"}, "cold": {"archive", "active"}, "archive": set()}
        if target_tier not in valid[current]:
            raise InvalidTransition(f"cannot move {current} to {target_tier}")
        changes = {"storage_tier": target_tier}
        now = time.time()
        if current == "active" and target_tier == "cold":
            if row["memory_class"] not in ("short_term", "process"):
                raise InvalidTransition("only short_term/process memories may enter cold storage")
            changes["cold_at"] = now
        elif current == "cold" and target_tier == "archive":
            changes["compressed_at"] = now
            changes["memory_class"] = "compressed_archive"
        elif current == "cold" and target_tier == "active":
            changes["memory_class"] = "short_term"
            changes["cold_at"] = None
        version = self.update_memory_lifecycle(
            memory_id, changes, expected_version, actor=actor, reason=reason or f"migrate {current} to {target_tier}",
        )
        if target_tier == "archive":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO memory_archives "
                    "(id,memory_id,archive_kind,summary,source_refs,period_start,period_end,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), memory_id, "compressed", row["content"],
                     _json([row["source_ref"]] if row["source_ref"] else []),
                     row["event_time_start"], row["event_time_end"], now),
                )
        return self.get_memory(memory_id) | {"revision_version": version}

    def restore_memory(self, memory_id, expected_version, actor="main_agent", reason=""):
        return self.migrate_memory(memory_id, "active", expected_version, actor=actor, reason=reason or "restore cold memory")

    def resolve_dispute(self, memory_id, action, expected_version, actor="user", changes=None, replacement_id=None, reason=""):
        if actor != "user":
            raise PermissionDenied("only the user may resolve a disputed memory")
        if action not in ("update", "supersede", "restore"):
            raise DomainError("action must be update, supersede, or restore")
        changes = dict(changes or {})
        changes["disputed"] = 0
        changes["confirmation_status"] = "confirmed"
        if action == "supersede":
            if replacement_id is None:
                raise DomainError("replacement_id is required to supersede a memory")
            changes["decision_status"] = "superseded"
            changes["supersedes_id"] = int(replacement_id)
        elif action == "restore":
            changes.setdefault("decision_status", "none")
        return self.update_memory_lifecycle(
            memory_id, changes, expected_version, actor=actor,
            reason=reason or f"user {action} disputed memory",
        )

    def mark_disputed(self, memory_id, expected_version, actor="main_agent", reason=""):
        if actor not in MEMORY_ACTORS:
            raise PermissionDenied(f"unknown memory actor: {actor}")
        return self.update_memory_lifecycle(
            memory_id, {"disputed": 1, "decision_status": "pending"}, expected_version,
            actor=actor, reason=reason or "memory marked disputed",
        )

    def recall(self, query, expand_to="active", limit=10, include_disputed=True):
        if not query or not query.strip():
            raise DomainError("query is required")
        if expand_to not in STORAGE_TIERS:
            raise DomainError(f"unknown recall tier: {expand_to}")
        tiers = STORAGE_TIERS[:STORAGE_TIERS.index(expand_to) + 1]
        limit = max(1, min(int(limit), 100))
        pattern = f"%{query.strip()[:200]}%"
        result = []
        with self._connect() as conn:
            for tier in tiers:
                rows = conn.execute(
                    "SELECT * FROM documents WHERE storage_tier=? AND "
                    "(content LIKE ? OR key_facts LIKE ? OR keywords LIKE ?) "
                    "ORDER BY importance DESC, updated_at DESC, id DESC LIMIT ?",
                    (tier, pattern, pattern, pattern, limit),
                ).fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    if not include_disputed and item.get("disputed"):
                        continue
                    item["dispute_warning"] = bool(item.get("disputed"))
                    items.append(item)
                result.append({"tier": tier, "results": items})
        return {"query": query, "expanded_to": expand_to, "tiers": result,
                "results": [item for group in result for item in group["results"]]}
