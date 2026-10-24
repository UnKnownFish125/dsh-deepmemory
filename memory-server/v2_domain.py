"""deepmemory v2 data contracts and lifecycle primitives.

This module deliberately depends only on Python's standard library. The
existing server owns HTTP, vector search, graph, backup, and archive behavior;
this module owns the P1 transactional data model that later API work can call.
"""

import hashlib
import json
import sqlite3
import sys
import time
import uuid

from sensitive import redact_text, redact_value, sensitivity_types


V2_SCHEMA_VERSION = 6

TASK_STATUSES = ("draft", "planned", "todo", "in_progress", "review", "completed", "failed")
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
LIBRARY_NAMES = ("bias", "core", "eco", "project", "runtime")
BIAS_MIN_IMPORTANCE = 0.8
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
SCOPES = ("global", "workspace", "session")
# 需要脱敏的正文类字段：v2 的每条写入路径（lifecycle / 状态卡 / 导入）都必须走
# redact_fields，禁止再直接把这些字段写进 documents。
MEMORY_TEXT_FIELDS = ("content", "key_facts", "persona_summary", "canonical_summary", "keywords")


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


MEMORY_UPDATE_FIELDS = frozenset({
    "content",
    "key_facts",
    "persona_summary",
    "canonical_summary",
    "keywords",
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
})
# 插入路径额外允许库/敏感标记（library 是入库属性，敏感标记由脱敏结果决定下限）。
MEMORY_INSERT_FIELDS = MEMORY_UPDATE_FIELDS | frozenset({
    "uuid", "library", "has_sensitive", "sensitive_types",
})


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_payload(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def redact_fields(fields, text_fields=MEMORY_TEXT_FIELDS):
    """Redact sensitive literals inside memory text fields.

    Returns ``(clean_fields, matches)``. Only string values are examined; this is
    the single gate every v2 memory write path must pass through before values
    reach ``documents`` / ``memory_revisions``.
    """
    clean = dict(fields or {})
    matches = []
    for name in text_fields:
        value = clean.get(name)
        if not isinstance(value, str):
            continue
        redacted, found = redact_text(value)
        if found:
            clean[name] = redacted
            matches.extend(found)
    return clean, matches


def redact_payload(payload):
    """Recursively redact a JSON-like state-card payload (keeps the structure)."""
    return redact_value(payload)



def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_columns(conn, table, columns):
    present = _columns(conn, table)
    for name, definition in columns:
        if name not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            present.add(name)


def _migrate_state_cards_v6(conn):
    """Make state cards workspace-owned: add workspace_id + composite UNIQUE.

    v5 keyed cards by ``(card_key, kind)``, which made two workspaces sharing a
    session_id collide. v6 scopes the key by workspace. SQLite cannot alter a
    UNIQUE constraint in place, so the (small) parent table is rebuilt while
    keeping every row id, so ``state_card_revisions.card_id`` references stay
    valid. Idempotent: detected by the composite UNIQUE in the stored DDL.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='state_cards'"
    ).fetchone()
    if row is None:
        return
    sql = row[0] or ""
    columns = _columns(conn, "state_cards")
    if "workspace_id" in columns and "UNIQUE(workspace_id, card_key, kind)" in sql:
        return
    ws_expr = "COALESCE(workspace_id,'')" if "workspace_id" in columns else "''"
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            f"""
            CREATE TABLE state_cards_v6 (
              id TEXT PRIMARY KEY,
              card_key TEXT NOT NULL,
              workspace_id TEXT NOT NULL DEFAULT '',
              session_id TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL CHECK(kind IN ('task','daily')),
              task_id TEXT REFERENCES tasks(id),
              payload TEXT NOT NULL DEFAULT '{{}}',
              version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
              current_revision_id TEXT,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              UNIQUE(workspace_id, card_key, kind)
            );
            INSERT OR IGNORE INTO state_cards_v6
              (id,card_key,workspace_id,session_id,kind,task_id,payload,version,
               current_revision_id,created_at,updated_at)
            SELECT id,card_key,{ws_expr},session_id,kind,task_id,payload,version,
               current_revision_id,created_at,updated_at
            FROM state_cards;
            DROP TABLE state_cards;
            ALTER TABLE state_cards_v6 RENAME TO state_cards;
            CREATE INDEX IF NOT EXISTS idx_cards_task ON state_cards(task_id);
            CREATE INDEX IF NOT EXISTS idx_cards_session ON state_cards(session_id,kind);
            CREATE INDEX IF NOT EXISTS idx_cards_workspace
              ON state_cards(workspace_id,session_id,kind);
            """
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")



V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  parent_task_id TEXT REFERENCES tasks(id),
  workspace_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  task_color TEXT NOT NULL DEFAULT 'neutral',
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK(status IN ('draft','planned','todo','in_progress','review','completed','failed')),
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
  workspace_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK(kind IN ('task','daily')),
  task_id TEXT REFERENCES tasks(id),
  payload TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  current_revision_id TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(workspace_id, card_key, kind)
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

CREATE TABLE IF NOT EXISTS session_keys (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  rotated_at REAL,
  revoked_at REAL,
  updated_at REAL,
  UNIQUE(workspace_id, session_id)
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
    ("library", "TEXT NOT NULL DEFAULT 'runtime'"),
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
  SELECT CASE WHEN NEW.library NOT IN ('bias','core','eco','project','runtime')
    THEN RAISE(ABORT, 'invalid library') END;
  SELECT CASE WHEN NEW.library = 'bias' AND (NEW.scope != 'global' OR NEW.importance < 0.8)
    THEN RAISE(ABORT, 'bias library requires scope=global and importance>=0.8') END;
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
  SELECT CASE WHEN NEW.library NOT IN ('bias','core','eco','project','runtime')
    THEN RAISE(ABORT, 'invalid library') END;
  SELECT CASE WHEN NEW.library = 'bias' AND (NEW.scope != 'global' OR NEW.importance < 0.8)
    THEN RAISE(ABORT, 'bias library requires scope=global and importance>=0.8') END;
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


DOCUMENT_LINKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS document_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL REFERENCES documents(id),
  doc_path TEXT NOT NULL,
  doc_kind TEXT NOT NULL DEFAULT 'note'
    CHECK(doc_kind IN ('contract','plan','proposal','code','note')),
  doc_version TEXT NOT NULL DEFAULT '',
  relation TEXT NOT NULL
    CHECK(relation IN ('derived_from','summarized_by','superseded_by')),
  workspace_id TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_document_links_doc ON document_links(doc_path);
CREATE INDEX IF NOT EXISTS idx_document_links_mem ON document_links(memory_id);
"""


def install_v2_schema(conn):
    """Install or repair the P1 schema without replacing legacy objects."""
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(V2_SCHEMA)
    state_cards_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='state_cards'"
    ).fetchone()
    if state_cards_sql and "kind != 'task' OR task_id IS NOT NULL" in (state_cards_sql[0] or ""):
        # v3 required task cards to point at a task. v4 makes every card
        # conversation-owned, so task_id becomes optional metadata. SQLite
        # cannot drop CHECK constraints in place; rebuild the small parent table.
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            CREATE TABLE state_cards_v4 (
              id TEXT PRIMARY KEY,
              card_key TEXT NOT NULL,
              session_id TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL CHECK(kind IN ('task','daily')),
              task_id TEXT REFERENCES tasks(id),
              payload TEXT NOT NULL DEFAULT '{}',
              version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
              current_revision_id TEXT,
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL,
              UNIQUE(card_key, kind)
            );
            INSERT INTO state_cards_v4
              (id,card_key,session_id,kind,task_id,payload,version,current_revision_id,created_at,updated_at)
            SELECT id,card_key,card_key,kind,task_id,payload,version,current_revision_id,created_at,updated_at
            FROM state_cards;
            DROP TABLE state_cards;
            ALTER TABLE state_cards_v4 RENAME TO state_cards;
            CREATE INDEX IF NOT EXISTS idx_cards_task ON state_cards(task_id);
            CREATE INDEX IF NOT EXISTS idx_cards_session ON state_cards(session_id,kind);
            """
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    if "documents" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "documents", DOCUMENT_COLUMNS)
        _ensure_columns(conn, "documents", (
            ("topic_id", "TEXT NOT NULL DEFAULT ''"),
            ("event_time", "REAL"),
        ))
        conn.executescript(VALIDATION_TRIGGERS)
        conn.executescript(DOCUMENT_LINKS_SCHEMA)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_v2_lifecycle "
            "ON documents(storage_tier, memory_class, decision_status, disputed)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_v2_task ON documents(task_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_library ON documents(library)"
        )
        # 迁移补默认：遗留空 memory_class 视为 semantic（测试与契约期望）
        conn.execute(
            "UPDATE documents SET memory_class='semantic' WHERE memory_class IS NULL OR memory_class=''"
        )
        conn.commit()
    if "sources" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "sources", SOURCE_COLUMNS)
    if "tasks" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "tasks", (
            ("task_color", "TEXT NOT NULL DEFAULT 'neutral'"),
            ("workspace_id", "TEXT NOT NULL DEFAULT ''"),
            ("session_id", "TEXT NOT NULL DEFAULT ''"),
            ("deleted_at", "REAL"),
            ("sol_advice", "TEXT NOT NULL DEFAULT ''"),
        ))
        tasks_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
        ).fetchone()
        if tasks_sql and ("draft" not in (tasks_sql[0] or "") or "review" not in (tasks_sql[0] or "")):
            conn.commit()
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript("CREATE TABLE tasks_v5 (\n                  id TEXT PRIMARY KEY,\n                  parent_task_id TEXT REFERENCES tasks(id),\n                  workspace_id TEXT NOT NULL DEFAULT '',\n                  session_id TEXT NOT NULL DEFAULT '',\n                  title TEXT NOT NULL,\n                  description TEXT NOT NULL DEFAULT '',\n                  task_color TEXT NOT NULL DEFAULT 'neutral',\n                  status TEXT NOT NULL DEFAULT 'planned'\n                    CHECK(status IN ('draft','planned','todo','in_progress','review','completed','failed')),\n                  blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)),\n                  block_reason TEXT NOT NULL DEFAULT '',\n                  missing_conditions TEXT NOT NULL DEFAULT '[]',\n                  completion_criteria TEXT NOT NULL DEFAULT '',\n                  failure_reason TEXT NOT NULL DEFAULT '',\n                  failure_evidence TEXT NOT NULL DEFAULT '',\n                  attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt >= 1),\n                  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),\n                  source_message_id TEXT NOT NULL DEFAULT '',\n                  trace_id TEXT NOT NULL DEFAULT '',\n                  created_at REAL NOT NULL,\n                  updated_at REAL NOT NULL,\n                  CHECK(blocked = 0 OR status = 'in_progress')\n                );\n                INSERT INTO tasks_v5\n                  (id,parent_task_id,workspace_id,session_id,title,description,task_color,status,\n                   blocked,block_reason,missing_conditions,completion_criteria,failure_reason,\n                   failure_evidence,attempt,version,source_message_id,trace_id,created_at,updated_at)\n                SELECT id,parent_task_id,workspace_id,session_id,title,description,task_color,status,\n                   blocked,block_reason,missing_conditions,completion_criteria,failure_reason,\n                   failure_evidence,attempt,version,source_message_id,trace_id,created_at,updated_at\n                FROM tasks;\n                DROP TABLE tasks;\n                ALTER TABLE tasks_v5 RENAME TO tasks;\n                CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);\n                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, blocked);\n                CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id,status,updated_at);\n                CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id,updated_at);")
            conn.commit()
            conn.execute("PRAGMA foreign_keys=ON")
        _ensure_columns(conn, "tasks", (("deleted_at", "REAL"), ("sol_advice", "TEXT NOT NULL DEFAULT ''")))
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_workspace "
            "ON tasks(workspace_id,status,updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id,updated_at)"
        )
    if "state_cards" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "state_cards", (("session_id", "TEXT NOT NULL DEFAULT ''"),))
        conn.execute("UPDATE state_cards SET session_id=card_key WHERE session_id='' OR session_id IS NULL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_session ON state_cards(session_id,kind)"
        )
        _migrate_state_cards_v6(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cards_workspace "
            "ON state_cards(workspace_id, session_id, kind)"
        )
    if "session_keys" in {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }:
        _ensure_columns(conn, "session_keys", (
            ("updated_at", "REAL"),
            ("rotated_at", "REAL"),
            ("revoked_at", "REAL"),
        ))


class V2Store:
    """Transactional task, card, and memory lifecycle operations."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.index_hook = None
        self.cache_hook = None

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def migrate(self):
        with self._connect() as conn:
            install_v2_schema(conn)

    # ---------------------------------------------------------------- unified write gate

    def set_write_hooks(self, index_hook=None, cache_hook=None):
        """Register post-write hooks.

        ``index_hook`` is the P1 vector/BM25 synchronization point (left as a
        no-op stub by this task); ``cache_hook`` invalidates derived read caches
        (the HTTP layer wires it to its search cache + semantic generation).
        """
        self.index_hook = index_hook
        self.cache_hook = cache_hook

    def _notify_write(self, event):
        for hook in (self.index_hook, self.cache_hook):
            if hook is None:
                continue
            try:
                hook(dict(event))
            except Exception as exc:  # pragma: no cover - a hook must never break a write
                print(f"[v2_domain] write hook failed: {exc}", file=sys.stderr)

    def _resolve_workspace_id(self, workspace_id, session_id, conn=None):
        """Resolve the owning workspace: explicit arg > unique session_keys match > ''.

        Kept deliberately permissive for legacy callers: when no workspace is
        supplied and ``session_keys`` does not disambiguate the session, the old
        workspace-agnostic semantics are preserved (an empty scope).
        """
        workspace_id = str(workspace_id or "").strip()
        if workspace_id:
            return workspace_id
        session_id = str(session_id or "").strip()
        if not session_id:
            return ""
        sql = (
            "SELECT DISTINCT workspace_id FROM session_keys "
            "WHERE session_id=? AND workspace_id!=''"
        )
        if conn is not None:
            rows = conn.execute(sql, (session_id,)).fetchall()
        else:
            with self._connect() as own:
                rows = own.execute(sql, (session_id,)).fetchall()
        found = {str(row[0]) for row in rows if row[0]}
        return found.pop() if len(found) == 1 else ""

    @staticmethod
    def _validate_scope(scope, workspace_id, session_id):
        if scope not in SCOPES:
            raise DomainError(f"unknown memory scope: {scope}")
        if scope == "workspace" and not workspace_id:
            raise DomainError("workspace-scoped memory requires workspace_id")
        if scope == "session" and not session_id:
            raise DomainError("session-scoped memory requires session_id")

    def write_memory(
        self,
        fields,
        memory_id=None,
        *,
        workspace_id=None,
        session_id=None,
        expected_version=None,
        actor="main_agent",
        reason="",
        source_message_id="",
        trace_id="",
        explicit_execution=False,
    ):
        """The single v2 memory write gate.

        Every v2 memory write (lifecycle updates, imports, state-card imports)
        must go through here. It applies, in order: redaction (``redact_text`` /
        ``sensitivity_types``) → workspace/session scope validation → the
        ``documents`` write → an immutable ``memory_revisions`` row → the index
        hook (P1 stub) → the cache-invalidation hook. Returns a small result dict;

        ``memory_id=None`` performs an insert (revision version 1), otherwise an
        optimistic-concurrency update against ``expected_version``.
        """
        fields = dict(fields or {})
        inserting = memory_id is None
        allowed = MEMORY_INSERT_FIELDS if inserting else MEMORY_UPDATE_FIELDS
        unknown = set(fields) - allowed
        if unknown:
            raise DomainError(f"unsupported memory fields: {sorted(unknown)}")
        if not fields:
            raise DomainError("memory write requires at least one field")
        if actor not in MEMORY_ACTORS:
            raise PermissionDenied(f"unknown memory actor: {actor}")
        decision = fields.get("decision_status")
        if decision == "invalid" and actor != "user":
            raise PermissionDenied("only an explicit user action may set invalid")
        if decision == "adopted" and actor == "model" and not explicit_execution:
            raise PermissionDenied("candidate discussion cannot be promoted to adopted")
        if decision is not None and decision not in DECISION_STATUSES:
            raise DomainError(f"unknown decision status: {decision}")
        if "memory_class" in fields and fields["memory_class"] not in MEMORY_CLASSES:
            raise DomainError(f"unknown memory_class: {fields['memory_class']}")
        if "storage_tier" in fields and fields["storage_tier"] not in STORAGE_TIERS:
            raise DomainError(f"unknown storage_tier: {fields['storage_tier']}")
        if "sensitivity_level" in fields and fields["sensitivity_level"] not in SENSITIVITY_LEVELS:
            raise DomainError(f"unknown sensitivity_level: {fields['sensitivity_level']}")

        # ① 脱敏：正文类字段统一过 redact_text；检测到敏感内容时 has_sensitive 只能置位。
        clean, matches = redact_fields(fields)
        detected = sensitivity_types(matches)
        if detected:
            clean["has_sensitive"] = 1
            clean["sensitive_types"] = json.dumps(detected, ensure_ascii=False)
        elif "sensitive_types" in clean and not isinstance(clean["sensitive_types"], str):
            clean["sensitive_types"] = json.dumps(clean["sensitive_types"] or [], ensure_ascii=False)
        if "has_sensitive" in clean:
            clean["has_sensitive"] = int(bool(clean["has_sensitive"]))

        if not inserting:
            return self._write_memory_update(
                int(memory_id), clean, matches, detected,
                workspace_id=workspace_id, session_id=session_id,
                expected_version=expected_version, actor=actor, reason=reason,
                source_message_id=source_message_id, trace_id=trace_id,
            )
        return self._write_memory_insert(
            clean, matches, detected,
            workspace_id=workspace_id, session_id=session_id, actor=actor,
            reason=reason, source_message_id=source_message_id, trace_id=trace_id,
        )

    def _write_memory_update(
        self, memory_id, clean, matches, detected,
        workspace_id=None, session_id=None, expected_version=None,
        actor="main_agent", reason="", source_message_id="", trace_id="",
    ):
        if expected_version is None:
            raise DomainError("expected_version is required for a memory update")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (memory_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"memory not found: {memory_id}")
            columns = set(row.keys())
            # ③ 作用域校验：resolved workspace 与行归属不一致时拒绝跨 workspace 写入。
            row_ws = str(row["workspace_id"] or "") if "workspace_id" in columns else ""
            row_sid = str(row["session_id"] or "") if "session_id" in columns else ""
            row_scope = str(row["scope"] or "") if "scope" in columns else ""
            resolved_ws = self._resolve_workspace_id(workspace_id, row_sid, conn=conn)
            if resolved_ws and row_ws and resolved_ws != row_ws and row_scope != "global":
                raise PermissionDenied("memory belongs to another workspace")
            if "workspace_id" in clean:
                requested_ws = str(clean["workspace_id"] or "").strip()
                if resolved_ws and requested_ws and requested_ws != resolved_ws:
                    raise PermissionDenied("memory workspace cannot be rewritten across workspaces")
                clean["workspace_id"] = requested_ws or resolved_ws or row_ws
            if "session_id" in clean:
                requested_sid = str(clean["session_id"] or "").strip()
                if session_id and requested_sid and requested_sid != str(session_id).strip():
                    raise PermissionDenied("memory session does not match the request scope")
                clean["session_id"] = requested_sid or str(session_id or "")
            target_sid = str(clean.get("session_id") or row_sid or session_id or "").strip()
            target_ws = str(clean.get("workspace_id") or row_ws or resolved_ws or "").strip()
            scope_after = str(clean.get("scope") or row_scope or "session")
            if {"scope", "workspace_id", "session_id"} & set(clean):
                self._validate_scope(scope_after, target_ws, target_sid)
            if resolved_ws and not row_ws and scope_after != "global":
                # 历史无归属行在被作用域化编辑时补归属；revision patch 会记录这次绑定。
                clean["workspace_id"] = resolved_ws
            missing = [key for key in clean if key not in columns]
            if missing:
                raise DomainError(f"memory fields not supported by this database: {sorted(missing)}")
            if row["revision_version"] != expected_version:
                raise ConflictError(
                    f"memory version conflict: expected {expected_version}, "
                    f"current {row['revision_version']}"
                )
            # before_payload 也走脱敏：历史明文不得经 revision 再落一份。
            before = {key: row[key] for key in MEMORY_UPDATE_FIELDS if key in columns}
            before, _ = redact_fields(before)
            after = dict(before)
            after.update(clean)
            version = int(row["revision_version"]) + 1
            parent = conn.execute(
                "SELECT id FROM memory_revisions WHERE memory_id=? ORDER BY version DESC LIMIT 1",
                (memory_id,),
            ).fetchone()
            fields_written = sorted(clean)
            assignments = ",".join(f"{key}=?" for key in fields_written)
            conn.execute(
                f"UPDATE documents SET {assignments},revision_version=?,updated_at=? WHERE id=?",
                [clean[key] for key in fields_written] + [version, time.time(), memory_id],
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
                    _json(clean),
                    _json(before),
                    _json(after),
                    actor,
                    reason,
                    source_message_id or clean.get("source_message_id", ""),
                    trace_id or clean.get("trace_id", ""),
                    _hash_payload(before),
                    _hash_payload(after),
                    time.time(),
                ),
            )
        self._notify_write({
            "kind": "memory", "action": "update", "memory_id": memory_id,
            "workspace_id": target_ws, "session_id": target_sid,
        })
        return {
            "id": memory_id,
            "revision_version": version,
            "inserted": False,
            "redacted": bool(matches),
            "sensitive_types": detected,
        }

    def _write_memory_insert(
        self, clean, matches, detected,
        workspace_id=None, session_id=None, actor="main_agent", reason="",
        source_message_id="", trace_id="",
    ):
        content = str(clean.get("content") or "").strip()
        if not content:
            raise DomainError("content is required")
        clean["content"] = content
        declared_ws = str(clean.get("workspace_id") or "").strip()
        if workspace_id and declared_ws and declared_ws != str(workspace_id).strip():
            raise PermissionDenied("memory workspace does not match the request scope")
        target_session = str(session_id or clean.get("session_id") or "").strip()
        requested_ws = str(workspace_id or declared_ws or "").strip()
        resolved_ws = self._resolve_workspace_id(requested_ws, target_session)
        scope = str(clean.get("scope") or "session")
        self._validate_scope(scope, resolved_ws or requested_ws, target_session)
        now = time.time()
        values = {
            "uuid": str(clean.get("uuid") or uuid.uuid4()),
            "content": content,
            "key_facts": str(clean.get("key_facts") or ""),
            "persona_summary": str(clean.get("persona_summary") or ""),
            "canonical_summary": str(clean.get("canonical_summary") or ""),
            "type": str(clean.get("type") or "fact"),
            "domain": str(clean.get("domain") or "work"),
            "scope": scope,
            "workspace_id": resolved_ws or requested_ws,
            "session_id": target_session,
            "persona_id": str(clean.get("persona_id") or ""),
            "importance": float(clean.get("importance") if clean.get("importance") is not None else 0.5),
            "created_at": now,
            "last_access_at": now,
            "access_count": 0,
            "status": "active",
            "keywords": str(clean.get("keywords") or ""),
            "has_sensitive": int(clean.get("has_sensitive") or 0),
            "sensitive_types": (
                clean["sensitive_types"] if isinstance(clean.get("sensitive_types"), str)
                else json.dumps(clean.get("sensitive_types") or [], ensure_ascii=False)
            ),
            "topic_id": str(clean.get("topic_id") or ""),
            "event_time": clean.get("event_time") if clean.get("event_time") is not None else now,
            "library": str(clean.get("library") or "runtime"),
            "time_raw": str(clean.get("time_raw") or ""),
            "time_precision": str(clean.get("time_precision") or "unknown"),
            "time_confidence": float(clean.get("time_confidence") or 0.0),
            "time_inferred": int(clean.get("time_inferred") or 0),
            "sensitivity_level": str(clean.get("sensitivity_level") or "normal"),
            "confirmation_status": str(clean.get("confirmation_status") or "unconfirmed"),
            "source_quality": float(
                clean.get("source_quality") if clean.get("source_quality") is not None else 0.5
            ),
            "actionability": float(clean.get("actionability") or 0.0),
            "reject_penalty": float(clean.get("reject_penalty") or 0.0),
            "source_ref": str(clean.get("source_ref") or ""),
            "source_message_id": str(clean.get("source_message_id") or source_message_id or ""),
            "trace_id": str(clean.get("trace_id") or trace_id or ""),
            "memory_class": str(clean.get("memory_class") or "semantic"),
            "storage_tier": str(clean.get("storage_tier") or "active"),
            "revision_version": 1,
            "updated_at": now,
        }
        for key, value in clean.items():
            if key not in values:
                values[key] = value
        with self._connect() as conn:
            present = _columns(conn, "documents")
            # 调用方显式给的字段必须存在；内部默认值可略过（老库缺列不强行 ALTER）。
            missing = [key for key in clean if key not in present]
            if missing:
                raise DomainError(f"memory fields not supported by this database: {sorted(missing)}")
            values = {key: value for key, value in values.items() if key in present}
            columns = sorted(values)
            placeholders = ",".join("?" for _ in columns)
            cur = conn.execute(
                f"INSERT INTO documents ({','.join(columns)}) VALUES ({placeholders})",
                [values[key] for key in columns],
            )
            memory_id = int(cur.lastrowid)
            after_payload = {key: value for key, value in values.items() if key != "uuid"}
            conn.execute(
                "INSERT INTO memory_revisions (id,memory_id,parent_revision_id,version,patch,"
                "before_payload,after_payload,actor,reason,source_message_id,trace_id,before_hash,"
                "after_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    memory_id,
                    None,
                    1,
                    _json({"created": True}),
                    "{}",
                    _json(after_payload),
                    actor,
                    reason or "memory created",
                    values.get("source_message_id", ""),
                    values.get("trace_id", ""),
                    _hash_payload({}),
                    _hash_payload(after_payload),
                    now,
                ),
            )
        self._notify_write({
            "kind": "memory", "action": "insert", "memory_id": memory_id,
            "workspace_id": values.get("workspace_id", ""),
            "session_id": values.get("session_id", ""),
        })
        return {
            "id": memory_id,
            "revision_version": 1,
            "inserted": True,
            "redacted": bool(matches),
            "sensitive_types": detected,
        }

    def find_memory_by_content(self, session_id, content, workspace_id=None):
        """Find an active memory of one session store by exact (redacted) content."""
        session_id = str(session_id or "").strip()
        content = str(content or "").strip()
        if not session_id or not content:
            return None
        clauses = ["session_id=?", "status='active'", "content=?"]
        args = [session_id, content]
        workspace_id = str(workspace_id or "").strip()
        if workspace_id:
            clauses.append("workspace_id=?")
            args.append(workspace_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1",
                args,
            ).fetchone()
        return dict(row) if row is not None else None

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
        workspace_id = str(fields.get("workspace_id") or "").strip()
        session_id = str(fields.get("session_id") or "").strip()
        if not workspace_id:
            raise DomainError("tasks require workspace_id")
        if not session_id:
            raise DomainError("tasks require session_id")
        max_active = int(fields.get("max_active_tasks", 0) or 0)
        if max_active > 0:
            with self._connect() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) c FROM tasks WHERE workspace_id=? AND status IN "
                    "('planned','todo','in_progress','review')",
                    (workspace_id,),
                ).fetchone()["c"]
            if count >= max_active:
                raise InvalidTransition(
                    f"task limit reached: {count} active tasks in workspace (max {max_active}); "
                    "finish or archive some before adding more"
                )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id,parent_task_id,workspace_id,session_id,title,description,"
                "task_color,status,blocked,block_reason,missing_conditions,completion_criteria,"
                "source_message_id,trace_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    parent_task_id,
                    workspace_id,
                    session_id,
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
            "draft": {"planned"},
            "planned": {"todo"},
            "todo": {"in_progress"},
            "in_progress": {"review", "failed"},
            "review": {"completed", "failed"},
            "failed": {"draft", "todo", "in_progress"},
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

    def delete_task(self, task_id, actor="user", reason="deleted by user"):
        """Soft-delete one task: set deleted_at and record a status_changed event."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["deleted_at"] is not None:
                raise ConflictError("task already deleted")
            conn.execute(
                "UPDATE tasks SET deleted_at=?,version=version+1,updated_at=? WHERE id=?",
                (time.time(), time.time(), task_id),
            )
            self._task_event(
                conn,
                task_id,
                "status_changed",
                row["status"],
                row["status"],
                row["attempt"],
                {"reason": reason, "actor": actor},
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

    def set_task_sol_advice(self, task_id, advice, expected_version, actor="main_agent", reason=""):
        """Store the sol-provided advice for a failed task (one-time aid; versioned)."""
        if not advice or not str(advice).strip():
            raise DomainError("sol advice is required")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["version"] != expected_version:
                raise ConflictError(
                    f"task version conflict: expected {expected_version}, current {row['version']}"
                )
            conn.execute(
                "UPDATE tasks SET sol_advice=?,version=version+1,updated_at=? WHERE id=?",
                (str(advice).strip(), time.time(), task_id),
            )
            self._task_event(
                conn,
                task_id,
                "status_changed",
                row["status"],
                row["status"],
                row["attempt"],
                {"reason": reason or "sol advice recorded", "actor": actor},
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

    def list_tasks(self, workspace_id, status=None, parent_task_id=None, session_id=None, limit=100):
        """Return a bounded task-board view without exposing raw sqlite rows."""
        workspace_id = str(workspace_id or "").strip()
        if not workspace_id:
            raise DomainError("workspace_id is required")
        clauses, args = ["workspace_id=?", "deleted_at IS NULL"], [workspace_id]
        if status:
            if status not in TASK_STATUSES:
                raise DomainError(f"unknown task status: {status}")
            clauses.append("status=?")
            args.append(status)
        if parent_task_id is not None:
            clauses.append("parent_task_id=?")
            args.append(parent_task_id)
        if session_id is not None:
            clauses.append("session_id=?")
            args.append(str(session_id))
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

    def rebind_task(self, task_id, workspace_id, session_id, expected_version):
        """Rebind one task to a conversation inside its owning workspace."""
        workspace_id = str(workspace_id or "").strip()
        session_id = str(session_id or "").strip()
        if not workspace_id or not session_id:
            raise DomainError("workspace_id and session_id are required")
        with self._connect() as conn:
            row = conn.execute("SELECT version,workspace_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"task not found: {task_id}")
            if row["version"] != expected_version:
                raise ConflictError("task version conflict")
            if row["workspace_id"] and row["workspace_id"] != workspace_id:
                raise InvalidTransition("task workspace cannot be changed; only its conversation may be rebound")
            conn.execute(
                "UPDATE tasks SET workspace_id=?,session_id=?,version=version+1,updated_at=? WHERE id=?",
                (workspace_id, session_id, time.time(), task_id),
            )
        return self.get_task(task_id)

    # ---------------------------------------------------------------- session keys

    @staticmethod
    def _new_session_key(workspace_id, session_id):
        """Generate an opaque, URL-safe session key with a stable prefix.

        The full key is only ever exposed once (creation / rotate response);
        storage keeps only its SHA-256 together with the display prefix.
        """
        raw = uuid.uuid4().hex + uuid.uuid4().hex
        prefix = "dmk." + hashlib.sha256((workspace_id + "/" + session_id).encode("utf-8")).hexdigest()[:10]
        return prefix + "." + raw, prefix

    @staticmethod
    def _key_hash(key):
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get_session_key(self, workspace_id, session_id, issue=True):
        """Return the existing (or newly created) opaque key for one session memory store.

        The key itself is returned once at creation; callers normally receive the
        public descriptor (prefix) and only get the full key from creation/rotate
        responses or memory maintenance UI after authorized access.
        """
        workspace_id = str(workspace_id or "").strip()
        session_id = str(session_id or "").strip()
        if not workspace_id or not session_id:
            raise DomainError("workspace_id and session_id are required")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_keys WHERE workspace_id=? AND session_id=?",
                (workspace_id, session_id),
            ).fetchone()
            if row is not None and row["revoked_at"] is None:
                return dict(row)
            if not issue:
                return None
            # 同一 (workspace_id, session_id) 永远单行：有 revoked 行则就地替换 key。
            key, prefix = self._new_session_key(workspace_id, session_id)
            if row is not None:
                conn.execute(
                    "UPDATE session_keys SET key_hash=?,key_prefix=?,rotated_at=?,revoked_at=NULL,updated_at=? "
                    "WHERE id=?",
                    (self._key_hash(key), prefix, now, now, row["id"]),
                )
                row_id = str(row["id"])
            else:
                row_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO session_keys (id,workspace_id,session_id,key_hash,key_prefix,created_at,rotated_at,revoked_at) "
                    "VALUES (?,?,?,?,?,?,NULL,NULL)",
                    (row_id, workspace_id, session_id, self._key_hash(key), prefix, now),
                )
            return {"id": row_id, "workspace_id": workspace_id, "session_id": session_id, "key": key, "prefix": prefix, "created_at": now}

    def rotate_session_key(self, workspace_id, session_id):
        """Rotate the session key: revoke the old record and issue a fresh one."""
        workspace_id = str(workspace_id or "").strip()
        session_id = str(session_id or "").strip()
        if not workspace_id or not session_id:
            raise DomainError("workspace_id and session_id are required")
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM session_keys WHERE workspace_id=? AND session_id=?",
                (workspace_id, session_id),
            ).fetchone()
        if row is None:
            return self.get_session_key(workspace_id, session_id)
        key, prefix = self._new_session_key(workspace_id, session_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE session_keys SET key_hash=?,key_prefix=?,rotated_at=?,revoked_at=NULL,updated_at=? "
                "WHERE id=?",
                (self._key_hash(key), prefix, now, now, row["id"]),
            )
        return {"id": str(row["id"]), "workspace_id": workspace_id, "session_id": session_id, "key": key, "prefix": prefix, "created_at": now, "rotated": True}

    def resolve_session_key(self, key):
        """Resolve an opaque key to (workspace_id, session_id) or raise NotFoundError."""
        key = str(key or "").strip()
        if not key:
            raise DomainError("session key is required")
        digest = self._key_hash(key)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT workspace_id,session_id FROM session_keys WHERE key_hash=? AND revoked_at IS NULL",
                (digest,),
            ).fetchone()
        if row is None:
            raise NotFoundError("session key is invalid or rotated")
        return str(row["workspace_id"]), str(row["session_id"])

    # ---------------------------------------------------------------- session export / import / purge

    def export_session(self, workspace_id, session_id, include_sensitive=False):
        """Export one session's active memory store (memories + card) for migration.

        The export is scoped by ``(workspace_id, session_id)``: when a workspace
        is supplied (or can be resolved from ``session_keys``) only that
        workspace's rows are exported, so two workspaces that share one
        session_id cannot read each other's store.

        Unless ``include_sensitive`` is explicitly true, sensitive literals are
        redacted on the way out — including the state card payload, which is
        exported as a parsed object (never as an unredacted raw string).
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            raise DomainError("session_id is required")
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        clauses = ["session_id=?", "status='active'"]
        args = [session_id]
        if resolved_ws:
            clauses.append("workspace_id=?")
            args.append(resolved_ws)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY id",
                args,
            ).fetchall()
            card_clauses = ["session_id=?", "kind IN ('task','daily')"]
            card_args = [session_id]
            if resolved_ws:
                card_clauses.append("workspace_id=?")
                card_args.append(resolved_ws)
            card = conn.execute(
                f"SELECT * FROM state_cards WHERE {' AND '.join(card_clauses)} "
                "ORDER BY updated_at DESC LIMIT 1",
                card_args,
            ).fetchone()
        memories = []
        for r in rows:
            keys = r.keys()
            item = {
                "content": str(r["content"] or ""),
                "key_facts": str(r["key_facts"] or "") if "key_facts" in keys else "",
                "type": str(r["type"] or "fact"),
                "domain": str(r["domain"] or "work"),
                "scope": str(r["scope"] or "session"),
                "workspace_id": str(r["workspace_id"] or "") if "workspace_id" in keys else "",
                "importance": float(r["importance"] or 0.5),
                "keywords": str(r["keywords"] or "") if "keywords" in keys else "",
                "has_sensitive": bool(r["has_sensitive"]),
                "sensitive_types": json.loads(r["sensitive_types"] or "[]"),
            }
            if include_sensitive:
                item["persona_summary"] = str(r["persona_summary"] or "") if "persona_summary" in keys else ""
                item["canonical_summary"] = (
                    str(r["canonical_summary"] or "") if "canonical_summary" in keys else ""
                )
            else:
                # 即使历史行里残留明文，也绝不让它明文离开导出边界。
                for field in ("content", "key_facts", "keywords"):
                    item[field] = redact_text(item[field])[0]
            memories.append(item)
        card_out = None
        if card is not None:
            try:
                payload = json.loads(card["payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            if not include_sensitive:
                payload = redact_payload(payload)
            card_out = {
                "kind": card["kind"],
                "version": int(card["version"]),
                "payload": payload,
            }
        return {
            "format": "deepmemory-session-v1",
            "workspace_id": resolved_ws,
            "session_id": session_id,
            "include_sensitive": bool(include_sensitive),
            "exported_at": time.time(),
            "memories": memories,
            "card": card_out,
            "truncated": 0,
        }

    def import_session_memories(self, target_session_id, payload, mode="merge", workspace_id=None):
        """Import an exported session memory bundle into another session store.

        merge:   fingerprint-dedupe against existing active documents.
        replace: archive existing active documents of the target session first.

        The import is workspace-scoped: target workspace = explicit
        ``workspace_id`` > bundle ``workspace_id`` > unique ``session_keys``
        match. Every memory goes through :meth:`write_memory`, so redaction,
        scope validation, revisions and write hooks cannot be bypassed. Long
        content is kept in full: ``truncated`` always reports how many items
        were cut (0 today — nothing is silently dropped).
        """
        target_session_id = str(target_session_id or "").strip()
        if not target_session_id:
            raise DomainError("target session_id is required")
        memories = payload.get("memories") if isinstance(payload, dict) else None
        if not isinstance(memories, list):
            raise ValueError("payload.memories is required")
        bundle_ws = ""
        if isinstance(payload, dict):
            bundle_ws = str(payload.get("workspace_id") or "").strip()
        target_ws = self._resolve_workspace_id(
            str(workspace_id or "").strip() or bundle_ws, target_session_id
        )
        now = time.time()
        inserted = 0
        merged = 0
        replaced = 0
        truncated = 0
        if mode == "replace":
            clauses = ["session_id=?", "status='active'"]
            args = [target_session_id]
            if target_ws:
                clauses.append("workspace_id=?")
                args.append(target_ws)
            with self._connect() as conn:
                cur = conn.execute(
                    f"UPDATE documents SET status='archived',updated_at=? "
                    f"WHERE {' AND '.join(clauses)}",
                    [now] + args,
                )
                replaced = cur.rowcount
        for m in memories:
            if not isinstance(m, dict) or not str(m.get("content") or "").strip():
                continue
            # 先按最终落库文本（脱敏后）比对去重；write_memory 会再脱敏一次（幂等）。
            prepared, matches = redact_fields(dict(m))
            flagged = sensitivity_types(matches)
            content = str(prepared.get("content") or "").strip()
            if not content:
                continue
            if mode == "merge" and self.find_memory_by_content(
                target_session_id, content, workspace_id=target_ws
            ) is not None:
                merged += 1
                continue
            fields = {
                key: value
                for key, value in prepared.items()
                if key in MEMORY_INSERT_FIELDS and key not in ("uuid", "workspace_id")
            }
            if flagged:
                # 预脱敏已消费掉原文：把检测结果显式带给统一写入口，避免 has_sensitive 丢失。
                fields["has_sensitive"] = 1
                fields["sensitive_types"] = json.dumps(flagged, ensure_ascii=False)
            fields["session_id"] = target_session_id
            if target_ws:
                fields["workspace_id"] = target_ws
            result = self.write_memory(
                fields,
                workspace_id=target_ws or None,
                session_id=target_session_id,
                actor="system",
                reason=f"session import ({mode})",
            )
            if result.get("inserted"):
                inserted += 1
        card = payload.get("card") if isinstance(payload, dict) else None
        card_result = None
        if isinstance(card, dict) and card.get("payload") is not None:
            kind = card.get("kind") if card.get("kind") in CARD_KINDS else "daily"
            card_payload = self._normalize_imported_card(card)
            if card_payload is not None:
                try:
                    existing = self.get_state_card(
                        target_session_id, kind, workspace_id=target_ws or None
                    )
                    expected_version = existing["version"]
                except NotFoundError:
                    expected_version = 0
                card_result = self.put_state_card(
                    target_session_id,
                    kind,
                    card_payload,
                    expected_version=expected_version,
                    workspace_id=target_ws or None,
                    actor="system",
                    reason=f"session import ({mode})",
                )
        return {
            "inserted": inserted,
            "merged": merged,
            "replaced": replaced,
            "mode": mode,
            "workspace_id": target_ws,
            "card_imported": bool(card_result),
            "truncated": truncated,
            "max_content_chars": None,
        }

    @staticmethod
    def _normalize_imported_card(card):
        """Accept both the serialized v1 export shape and the parsed v2 shape."""
        if not isinstance(card, dict):
            return None
        payload = card.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                return None
        if not isinstance(payload, dict):
            return None
        return redact_payload(payload)

    def purge_session(self, session_id, hard=False, workspace_id=None):
        """归档（或物理删除）一个会话的记忆/状态卡/任务，保留审计。

        The purge is scoped by ``(workspace_id, session_id)`` when the workspace
        is known: a session id shared by two workspaces only ever purges its own
        store. Default semantics: 软归档（status='archived'），可恢复；hard=True
        物理删除主体行（不可变审计链 memory_revisions/state_card_revisions/
        task_events 保留）。
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            raise DomainError("session_id is required")
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        ws_and = " AND workspace_id=?" if resolved_ws else ""
        ws_args = [resolved_ws] if resolved_ws else []
        doc_where = "session_id=?" + ws_and
        doc_args = [session_id] + ws_args
        doc_sub = "SELECT id FROM documents WHERE " + doc_where
        now = time.time()
        with self._connect() as conn:
            docs = conn.execute(
                f"SELECT COUNT(*) c FROM documents WHERE {doc_where}", doc_args
            ).fetchone()["c"]
            cards = conn.execute(
                f"SELECT COUNT(*) c FROM state_cards WHERE {doc_where}", doc_args
            ).fetchone()["c"]
            tasks = conn.execute(
                f"SELECT COUNT(*) c FROM tasks WHERE {doc_where}", doc_args
            ).fetchone()["c"]
            if hard:
                # 物理删除：移除主体行（documents/atoms/graph_edges/state_cards/tasks），
                # 但保留不可变审计链（memory_revisions/state_card_revisions/task_events）——
                # 符合"保留 revisions（审计）仅删除主体行"的语义，purge 不再因
                # immutable delete trigger（memory/state_card/task_events 的 BEFORE DELETE
                # RAISE(ABORT)）而必败。
                #
                # 审计行经 FK 引用主体行（memory_revisions.memory_id → documents.id 等，
                # 且 memory_id 为 NOT NULL），保留审计必然留下指向已删主体的引用，因此
                # 本次 purge 事务临时禁用 FK 约束（仅本连接内生效，不改 schema；事务提交后
                # 新连接经 _connect() 恢复 PRAGMA foreign_keys=ON）。
                conn.execute("PRAGMA foreign_keys=OFF")
                # 级联清理全部主体行关联：子表先删（sources→protected_sources→
                # document_links→memory_archives→atoms→graph_edges→documents），仅删
                # 本会话（本 workspace）记忆的关联；审计链保留。
                for statement in (
                    "DELETE FROM sources WHERE memory_id IN (%s)" % doc_sub,
                    "DELETE FROM protected_sources WHERE memory_id IN (%s)" % doc_sub,
                    "DELETE FROM document_links WHERE memory_id IN (%s)" % doc_sub,
                    "DELETE FROM memory_archives WHERE memory_id IN (%s)" % doc_sub,
                    "DELETE FROM atoms WHERE memory_id IN (%s)" % doc_sub,
                    "DELETE FROM graph_edges WHERE memory_id IN (%s)" % doc_sub,
                ):
                    conn.execute(statement, doc_args)
                conn.execute(f"DELETE FROM documents WHERE {doc_where}", doc_args)
                conn.execute(f"DELETE FROM state_cards WHERE {doc_where}", doc_args)
                conn.execute(f"DELETE FROM tasks WHERE {doc_where}", doc_args)
            else:
                conn.execute(
                    f"UPDATE documents SET status='archived',updated_at=? "
                    f"WHERE {doc_where} AND status='active'",
                    [now] + doc_args,
                )
                # 任务无 archived 状态（v5 状态机不含'archived'）；会话软归档时把进行中/
                # 待办任务收尾为 completed（保留事件与溯源），避免看板残留未完成项。
                # blocked=1 时 status 必为 in_progress，直接改 completed 会违反 CHECK，
                # 因此同时置 blocked=0。
                conn.execute(
                    f"UPDATE tasks SET status='completed',blocked=0,version=version+1,updated_at=? "
                    f"WHERE {doc_where} AND status NOT IN ('completed')",
                    [now] + doc_args,
                )
            # 统一撤除会话 key（归档/删除后不再有迁移凭证）
            conn.execute(
                f"UPDATE session_keys SET revoked_at=? WHERE {doc_where} AND revoked_at IS NULL",
                [now] + doc_args,
            )
        self._notify_write({
            "kind": "session", "action": "purge", "session_id": session_id,
            "workspace_id": resolved_ws, "hard": bool(hard),
        })
        return {
            "session_id": session_id,
            "workspace_id": resolved_ws,
            "hard": hard,
            "documents": docs,
            "cards": cards,
            "tasks": tasks,
        }

    @staticmethod
    def _find_state_card(conn, session_id, kind, workspace_id=""):
        """Find the card for (workspace, session, kind), adopting legacy unbound rows."""
        if workspace_id:
            row = conn.execute(
                "SELECT * FROM state_cards WHERE workspace_id=? AND session_id=? AND kind=?",
                (workspace_id, session_id, kind),
            ).fetchone()
            if row is not None:
                return row
            # 旧库里无归属的卡片不再复制一份：直接沿用，写入时补 workspace 归属。
            return conn.execute(
                "SELECT * FROM state_cards WHERE workspace_id='' AND session_id=? AND kind=? "
                "ORDER BY version DESC, id LIMIT 1",
                (session_id, kind),
            ).fetchone()
        return conn.execute(
            "SELECT * FROM state_cards WHERE session_id=? AND kind=? "
            "ORDER BY (workspace_id='') DESC, updated_at DESC, id LIMIT 1",
            (session_id, kind),
        ).fetchone()

    @staticmethod
    def _card_row(row):
        result = dict(row)
        result["payload"] = json.loads(result["payload"] or "{}")
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
        workspace_id=None,
    ):
        if kind not in CARD_KINDS:
            raise DomainError(f"unknown card kind: {kind}")
        if actor not in CARD_ACTORS:
            raise PermissionDenied("sub-agent/model paths cannot mutate state cards")
        session_id = str(card_key or "").strip()
        if not session_id:
            raise DomainError("state cards require session_id")
        if not isinstance(payload, dict):
            raise DomainError("state card payload must be an object")
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        # 状态卡 payload 也必须在统一写入口脱敏（原实现直接落原始 payload）。
        clean_payload = redact_payload(payload)
        now = time.time()
        with self._connect() as conn:
            row = self._find_state_card(conn, session_id, kind, resolved_ws)
            before = json.loads(row["payload"]) if row else {}
            before = redact_payload(before)  # 旧明文 payload 不得经 revision 再落一份
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
            target_ws = resolved_ws or (str(row["workspace_id"] or "") if row else "")
            patch = {
                key: {"before": before.get(key), "after": clean_payload.get(key)}
                for key in sorted(set(before) | set(clean_payload))
                if before.get(key) != clean_payload.get(key)
            }
            revision_id = str(uuid.uuid4())
            if not row:
                # 先落卡片主体：state_card_revisions.card_id 有 FK，必须引用已存在行。
                conn.execute(
                    "INSERT INTO state_cards (id,card_key,workspace_id,session_id,kind,task_id,"
                    "payload,version,current_revision_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        card_id,
                        session_id,
                        target_ws,
                        session_id,
                        kind,
                        task_id,
                        _json(clean_payload),
                        version,
                        revision_id,
                        now,
                        now,
                    ),
                )
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
                    _json(clean_payload),
                    actor,
                    reason,
                    source_message_id,
                    tool_trace_id,
                    subagent_trace_id,
                    _hash_payload(before),
                    _hash_payload(clean_payload),
                    now,
                ),
            )
            if row:
                conn.execute(
                    "UPDATE state_cards SET workspace_id=?,task_id=?,payload=?,version=?,"
                    "current_revision_id=?,updated_at=? WHERE id=?",
                    (target_ws, task_id, _json(clean_payload), version, revision_id, now, card_id),
                )
            card = conn.execute("SELECT * FROM state_cards WHERE id=?", (card_id,)).fetchone()
        self._notify_write({
            "kind": "state_card", "action": "put", "card_id": card_id,
            "session_id": session_id, "workspace_id": target_ws,
        })
        return self._card_row(card)

    def get_state_card(self, card_key, kind, workspace_id=None):
        session_id = str(card_key or "").strip()
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        with self._connect() as conn:
            row = self._find_state_card(conn, session_id, kind, resolved_ws)
        if row is None:
            raise NotFoundError(f"state card not found: {kind}/{card_key}")
        return self._card_row(row)

    def state_card_revisions(self, card_key, kind, limit=100, workspace_id=None):
        session_id = str(card_key or "").strip()
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        with self._connect() as conn:
            card = self._find_state_card(conn, session_id, kind, resolved_ws)
            if card is None:
                raise NotFoundError(f"state card not found: {kind}/{card_key}")
            rows = conn.execute(
                "SELECT * FROM state_card_revisions WHERE card_id=? "
                "ORDER BY version DESC LIMIT ?",
                (card["id"], max(1, min(int(limit), 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("patch", "before_payload", "after_payload"):
                item[key] = json.loads(item[key] or "{}")
            result.append(item)
        return result

    def restore_state_card(self, card_key, kind, revision_id, expected_version,
                           workspace_id=None, **audit):
        session_id = str(card_key or "").strip()
        resolved_ws = self._resolve_workspace_id(workspace_id, session_id)
        with self._connect() as conn:
            card = self._find_state_card(conn, session_id, kind, resolved_ws)
            if card is None:
                raise NotFoundError("state card revision not found")
            revision = conn.execute(
                "SELECT after_payload FROM state_card_revisions WHERE id=? AND card_id=?",
                (revision_id, card["id"]),
            ).fetchone()
        if revision is None:
            raise NotFoundError("state card revision not found")
        audit.setdefault("reason", f"restore revision {revision_id}")
        return self.put_state_card(
            card_key,
            kind,
            json.loads(revision["after_payload"]),
            expected_version=expected_version,
            task_id=card["task_id"],
            workspace_id=resolved_ws or None,
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
        workspace_id=None,
        session_id=None,
    ):
        """Compatibility wrapper around :meth:`write_memory`.

        All policy/redaction/revision/hook work lives in the unified write gate;
        callers keep the historical ``revision_version`` return value.
        """
        result = self.write_memory(
            dict(changes or {}),
            memory_id,
            workspace_id=workspace_id,
            session_id=session_id,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
            source_message_id=source_message_id,
            trace_id=trace_id,
            explicit_execution=explicit_execution,
        )
        return result["revision_version"]

    def get_memory(self, memory_id, workspace_id=None):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=?", (int(memory_id),)).fetchone()
            if row is None:
                raise NotFoundError(f"memory not found: {memory_id}")
            row_ws = str(row["workspace_id"] or "") if "workspace_id" in row.keys() else ""
            resolved_ws = self._resolve_workspace_id(workspace_id, row["session_id"], conn=conn)
            if resolved_ws and row_ws not in ("", resolved_ws) and str(row["scope"] or "") != "global":
                raise NotFoundError(f"memory not found: {memory_id}")
            links = conn.execute(
                "SELECT id, doc_path, doc_kind, doc_version, relation, workspace_id, created_at "
                "FROM document_links WHERE memory_id=? ORDER BY id", (int(memory_id),),
            ).fetchall()
        return dict(row) | {"doc_links": [dict(l) for l in links]}

    def link_document(self, memory_id, doc_path, doc_kind="note", doc_version="",
                      relation="derived_from", workspace_id=""):
        """建/改 document_links：同一 memory+doc_path+relation 幂等更新。"""
        if not doc_path or not doc_path.strip():
            raise DomainError("doc_path is required")
        if doc_kind not in ("contract", "plan", "proposal", "code", "note"):
            raise DomainError(f"invalid doc_kind: {doc_kind}")
        if relation not in ("derived_from", "summarized_by", "superseded_by"):
            raise DomainError(f"invalid relation: {relation}")
        if memory_id <= 0 or not self.get_memory(memory_id):
            raise NotFoundError(f"memory not found: {memory_id}")
        now = time.time()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM document_links WHERE memory_id=? AND doc_path=? AND relation=?",
                (memory_id, doc_path, relation),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE document_links SET doc_kind=?, doc_version=?, workspace_id=? WHERE id=?",
                    (doc_kind, doc_version, workspace_id, existing["id"]),
                )
                link_id = existing["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO document_links (memory_id, doc_path, doc_kind, doc_version,"
                    " relation, workspace_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    (memory_id, doc_path, doc_kind, doc_version, relation, workspace_id, now),
                )
                link_id = cur.lastrowid
        return {"id": link_id, "memory_id": memory_id, "doc_path": doc_path,
                "doc_kind": doc_kind, "doc_version": doc_version, "relation": relation}

    def documents_for_path(self, doc_path, workspace_id=None):
        """文档→记忆反查：返回该 doc_path 全部 document_links 关联。"""
        if not doc_path or not doc_path.strip():
            raise DomainError("doc_path is required")
        with self._connect() as conn:
            if workspace_id:
                rows = conn.execute(
                    "SELECT dl.*, d.content FROM document_links dl "
                    "LEFT JOIN documents d ON d.id=dl.memory_id "
                    "WHERE dl.doc_path=? AND dl.workspace_id=? ORDER BY dl.id",
                    (doc_path, workspace_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT dl.*, d.content FROM document_links dl "
                    "LEFT JOIN documents d ON d.id=dl.memory_id "
                    "WHERE dl.doc_path=? ORDER BY dl.id", (doc_path,),
                ).fetchall()
        return [dict(r) for r in rows]

    def library_stats(self):
        """库目录：各 library 条目数 / 最新更新 / topic 分布。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT library, COUNT(*) AS total, "
                "SUM(CASE WHEN storage_tier='archive' THEN 1 ELSE 0 END) AS archived, "
                "MAX(updated_at) AS latest "
                "FROM documents GROUP BY library ORDER BY library"
            ).fetchall()
            topics = conn.execute(
                "SELECT library, topic_id, COUNT(*) AS n FROM documents "
                "WHERE topic_id!='' GROUP BY library, topic_id ORDER BY library, n DESC"
            ).fetchall()
        stats = {r["library"]: {"total": r["total"], "archived": r["archived"] or 0,
                                "latest": r["latest"]} for r in rows}
        for t in topics:
            lib = stats.setdefault(t["library"], {"total": 0, "archived": 0, "latest": None})
            lib.setdefault("topics", {})[t["topic_id"]] = t["n"]
        return stats

    def migrate_memory(self, memory_id, target_tier, expected_version, actor="main_agent",
                       reason="", workspace_id=None):
        """Perform one explicit lifecycle hop; callers cannot skip a tier."""
        row = self.get_memory(memory_id, workspace_id=workspace_id)
        current = row["storage_tier"]
        if target_tier not in STORAGE_TIERS:
            raise DomainError(f"unknown storage tier: {target_tier}")
        valid = {"active": {"cold"}, "cold": {"archive", "active"}, "archive": {"active"}}
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
        elif current == "archive" and target_tier == "active":
            # archive → active 恢复：memory_class 还原为 semantic（摘要保留在 memory_archives 供审计）
            changes["memory_class"] = "semantic"
            changes["compressed_at"] = None
        version = self.update_memory_lifecycle(
            memory_id, changes, expected_version, actor=actor,
            reason=reason or f"migrate {current} to {target_tier}",
            workspace_id=workspace_id,
        )
        if target_tier == "archive":
            with self._connect() as conn:
                # R3：source_refs 列表化——附带 sources 表实际来源行 id（分段后多条全含）；
                # 无 sources 行时回退旧单元素（documents.source_ref）
                src_ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM sources WHERE memory_id=?", (memory_id,)).fetchall()]
                refs = src_ids if src_ids else ([row["source_ref"]] if row["source_ref"] else [])
                conn.execute(
                    "INSERT INTO memory_archives "
                    "(id,memory_id,archive_kind,summary,source_refs,period_start,period_end,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), memory_id, "compressed", row["content"],
                     _json(refs),
                     row["event_time_start"], row["event_time_end"], now),
                )
        return self.get_memory(memory_id, workspace_id=workspace_id) | {"revision_version": version}

    def restore_memory(self, memory_id, expected_version, actor="main_agent", reason="",
                       workspace_id=None):
        return self.migrate_memory(
            memory_id, "active", expected_version, actor=actor,
            reason=reason or "restore cold memory", workspace_id=workspace_id,
        )

    def archive_library(self, library, topic=None, actor="main_agent", reason=""):
        """库级归档：直接批量置 storage_tier='archive'（不走 migrate 单跳），
        bias 库拒绝归档；批量写 memory_archives 摘要。返回归档条目数。"""
        if library not in LIBRARY_NAMES:
            raise DomainError(f"unknown library: {library}")
        if library == "bias":
            raise PermissionDenied("bias library may never be archived")
        now = time.time()
        archived_ids = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, source_ref, event_time_start, event_time_end FROM documents "
                "WHERE library=? AND (topic_id=? OR ? IS NULL) AND storage_tier!='archive'",
                (library, topic or "", topic),
            ).fetchall()
            for row in rows:
                archived_ids.append(row["id"])
                conn.execute(
                    "UPDATE documents SET storage_tier='archive', memory_class='compressed_archive',"
                    " compressed_at=?, updated_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
                conn.execute(
                    "INSERT INTO memory_archives "
                    "(id,memory_id,archive_kind,summary,source_refs,period_start,period_end,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), row["id"], "compressed",
                     row["content"] or "", _json([row["source_ref"]] if row["source_ref"] else []),
                     row["event_time_start"], row["event_time_end"], now),
                )
        return {"library": library, "topic": topic or "", "archived": len(archived_ids),
                "ids": archived_ids}

    def resolve_dispute(self, memory_id, action, expected_version, actor="user", changes=None,
                        replacement_id=None, reason="", workspace_id=None):
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
            workspace_id=workspace_id,
        )

    def mark_disputed(self, memory_id, expected_version, actor="main_agent", reason="",
                      workspace_id=None):
        if actor not in MEMORY_ACTORS:
            raise PermissionDenied(f"unknown memory actor: {actor}")
        return self.update_memory_lifecycle(
            memory_id, {"disputed": 1, "decision_status": "pending"}, expected_version,
            actor=actor, reason=reason or "memory marked disputed",
            workspace_id=workspace_id,
        )

    @staticmethod
    def _recall_scope(workspace_id, session_id):
        """Workspace/session visibility predicate for recall (scope_allows-like)."""
        workspace_id = str(workspace_id or "").strip()
        session_id = str(session_id or "").strip()
        if workspace_id and session_id:
            return (
                "(scope='global' OR (workspace_id=? AND (scope='workspace' OR "
                "(scope='session' AND session_id=?))) OR "
                "(workspace_id='' AND scope='session' AND session_id=?))",
                [workspace_id, session_id, session_id],
            )
        if workspace_id:
            return (
                "(scope='global' OR (workspace_id=? AND scope IN ('workspace','session')))",
                [workspace_id],
            )
        if session_id:
            return "(scope='global' OR (scope='session' AND session_id=?))", [session_id]
        return "", []

    def recall(self, query, expand_to="active", limit=10, include_disputed=True,
               workspace_id=None, session_id=None):
        if not query or not query.strip():
            raise DomainError("query is required")
        if expand_to not in STORAGE_TIERS:
            raise DomainError(f"unknown recall tier: {expand_to}")
        tiers = STORAGE_TIERS[:STORAGE_TIERS.index(expand_to) + 1]
        limit = max(1, min(int(limit), 100))
        pattern = f"%{query.strip()[:200]}%"
        scope_clause, scope_args = self._recall_scope(workspace_id, session_id)
        where = (
            "storage_tier=? AND (content LIKE ? OR key_facts LIKE ? OR keywords LIKE ?)"
        )
        args = [pattern, pattern, pattern]
        if scope_clause:
            where += f" AND {scope_clause}"
        result = []
        with self._connect() as conn:
            for tier in tiers:
                rows = conn.execute(
                    f"SELECT * FROM documents WHERE {where} "
                    "ORDER BY importance DESC, updated_at DESC, id DESC LIMIT ?",
                    [tier] + args + scope_args + [limit],
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
