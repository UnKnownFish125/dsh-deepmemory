import json
import os
import sqlite3
import sys
import tempfile
import unittest


SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER_DIR)

from v2_domain import (  # noqa: E402
    ConflictError,
    InvalidTransition,
    PermissionDenied,
    V2Store,
    install_v2_schema,
)


LEGACY_SCHEMA = """
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid TEXT UNIQUE NOT NULL,
  content TEXT NOT NULL,
  status TEXT DEFAULT 'active',
  type TEXT DEFAULT 'fact',
  domain TEXT DEFAULT 'work',
  scope TEXT DEFAULT 'session',
  workspace_id TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  persona_id TEXT DEFAULT '',
  importance REAL DEFAULT 0.5,
  has_sensitive INTEGER DEFAULT 0,
  sensitive_types TEXT DEFAULT '[]',
  memory_class TEXT DEFAULT '',
  storage_tier TEXT DEFAULT 'active',
  decision_status TEXT DEFAULT 'none',
  disputed INTEGER DEFAULT 0,
  time_inferred INTEGER DEFAULT 0,
  sensitivity_level TEXT DEFAULT 'normal',
  library TEXT NOT NULL DEFAULT 'runtime',
  topic_id TEXT DEFAULT '',
  event_time REAL
);
CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at REAL
);
CREATE TABLE graph_nodes (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE graph_edges (id INTEGER PRIMARY KEY, memory_id INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""


class V2DomainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(LEGACY_SCHEMA)
            conn.execute(
                "INSERT INTO documents (uuid,content,status) VALUES ('legacy','kept','active')"
            )
        self.store = V2Store(self.db_path)
        self.store.migrate()

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_is_idempotent_and_preserves_legacy_data(self):
        self.store.migrate()
        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            row = conn.execute("SELECT uuid,content,status,memory_class FROM documents").fetchone()
        self.assertTrue({"memory_class", "storage_tier", "decision_status", "revision_version"} <= columns)
        self.assertTrue({"tasks", "task_events", "state_cards", "memory_revisions"} <= tables)
        self.assertEqual(("legacy", "kept", "active", "semantic"), row)

    def test_task_constraints_blocking_and_subtasks(self):
        parent = self.store.create_task("parent", workspace_id="ws-1", session_id="session-1")
        child = self.store.create_task("child", parent_task_id=parent["id"], status="todo", workspace_id="ws-1", session_id="session-1")
        self.assertEqual(parent["id"], child["parent_task_id"])
        with self.assertRaises(InvalidTransition):
            self.store.set_task_blocked(child["id"], True, child["version"], "waiting")
        child = self.store.transition_task(child["id"], "in_progress", child["version"])
        child = self.store.set_task_blocked(
            child["id"], True, child["version"], "permission", ["owner approval"]
        )
        self.assertTrue(child["blocked"])
        self.assertEqual(["owner approval"], child["missing_conditions"])
        with sqlite3.connect(self.db_path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tasks (id,title,status,created_at,updated_at) "
                    "VALUES ('bad','bad','unknown',0,0)"
                )

    def test_task_color_is_persistent_validated_and_versioned(self):
        task = self.store.create_task("colored", task_color="blue", workspace_id="ws-1", session_id="session-1")
        self.assertEqual("blue", task["task_color"])
        task = self.store.set_task_color(task["id"], "red", task["version"])
        self.assertEqual("red", task["task_color"])
        with self.assertRaises(ConflictError):
            self.store.set_task_color(task["id"], "green", 1)
        with self.assertRaises(InvalidTransition):
            self.store.set_task_color(task["id"], "purple", task["version"])

    def test_task_board_is_workspace_scoped_and_task_rebinds_conversation(self):
        first = self.store.create_task(
            "workspace one", workspace_id="ws-1", session_id="session-1"
        )
        self.store.create_task(
            "workspace two", workspace_id="ws-2", session_id="session-2"
        )
        self.assertEqual([first["id"]], [item["id"] for item in self.store.list_tasks("ws-1")])
        rebound = self.store.rebind_task(first["id"], "ws-1", "session-3", first["version"])
        self.assertEqual("ws-1", rebound["workspace_id"])
        self.assertEqual("session-3", rebound["session_id"])
        with self.assertRaises(ConflictError):
            self.store.rebind_task(first["id"], "ws-1", "session-4", first["version"])
        with self.assertRaises(InvalidTransition):
            self.store.rebind_task(first["id"], "ws-2", "session-4", rebound["version"])

    def test_failure_and_reopen_history_is_append_only(self):
        task = self.store.create_task("retry", status="todo", workspace_id="ws-1", session_id="session-1")
        task = self.store.transition_task(task["id"], "in_progress", task["version"])
        task = self.store.transition_task(
            task["id"], "failed", task["version"], reason="test failed", evidence="trace-1"
        )
        task = self.store.transition_task(
            task["id"], "todo", task["version"], reason="dependency fixed"
        )
        self.assertEqual(2, task["attempt"])
        self.assertEqual("test failed", task["failure_reason"])
        events = self.store.task_history(task["id"])
        self.assertEqual(["created", "status_changed", "failed", "reopened"], [e["event_type"] for e in events])
        self.assertEqual("trace-1", events[2]["evidence"])
        with sqlite3.connect(self.db_path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM task_events WHERE id=?", (events[2]["id"],))

    def test_task_and_daily_card_revisions_detect_conflicts(self):
        task = self.store.create_task("card owner", workspace_id="ws-1", session_id="session-1")
        card = self.store.put_state_card(
            "session-1", "task", {"goal": "ship"}, task_id=task["id"], source_message_id="m1"
        )
        card = self.store.put_state_card(
            "session-1",
            "task",
            {"goal": "ship", "next_steps": ["test"]},
            expected_version=card["version"],
            task_id=task["id"],
            tool_trace_id="tool-1",
        )
        self.store.put_state_card("daily-1", "daily", {"topic": "sqlite"})
        with self.assertRaises(ConflictError):
            self.store.put_state_card(
                "session-1", "task", {"goal": "overwrite"}, expected_version=1, task_id=task["id"]
            )
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            revisions = conn.execute(
                "SELECT * FROM state_card_revisions WHERE card_id=? ORDER BY version", (card["id"],)
            ).fetchall()
            self.assertEqual(2, len(revisions))
            self.assertEqual("tool-1", revisions[1]["tool_trace_id"])
            self.assertNotEqual(revisions[1]["before_hash"], revisions[1]["after_hash"])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE state_card_revisions SET reason='changed' WHERE id=?", (revisions[0]["id"],))

    def test_task_state_card_is_session_bound_without_task_dependency(self):
        card = self.store.put_state_card("session-only", "task", {"goal": "ship"})
        self.assertEqual("session-only", card["session_id"])
        self.assertIsNone(card["task_id"])
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT card_key,session_id FROM state_cards WHERE id=?", (card["id"],)
            ).fetchone()
        self.assertEqual(("session-only", "session-only"), row)

    def test_invalid_and_candidate_adoption_require_authority(self):
        with self.assertRaises(PermissionDenied):
            self.store.update_memory_lifecycle(
                1, {"decision_status": "invalid"}, expected_version=0, actor="model"
            )
        with self.assertRaises(PermissionDenied):
            self.store.update_memory_lifecycle(
                1, {"decision_status": "adopted"}, expected_version=0, actor="model"
            )
        version = self.store.update_memory_lifecycle(
            1, {"decision_status": "exploring"}, expected_version=0, actor="model"
        )
        self.assertEqual(1, version)
        version = self.store.update_memory_lifecycle(
            1, {"decision_status": "invalid"}, expected_version=1, actor="user", reason="explicit"
        )
        self.assertEqual(2, version)

    def test_memory_revisions_are_optimistic_and_immutable(self):
        version = self.store.update_memory_lifecycle(
            1,
            {
                "memory_class": "process",
                "storage_tier": "cold",
                "time_raw": "上周",
                "event_time_start": 10.0,
                "event_time_end": 20.0,
                "time_confidence": 0.7,
                "source_ref": "source:1",
                "trace_id": "trace:1",
            },
            expected_version=0,
            actor="main_agent",
        )
        with self.assertRaises(ConflictError):
            self.store.update_memory_lifecycle(
                1, {"disputed": 1}, expected_version=0, actor="main_agent"
            )
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT patch,trace_id FROM memory_revisions").fetchone()
            self.assertEqual("trace:1", row[1])
            self.assertEqual("process", json.loads(row[0])["memory_class"])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM memory_revisions")
        self.assertEqual(1, version)


class EmptyMigrationTest(unittest.TestCase):
    def test_installer_can_repeat_on_new_server_schema(self):
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(LEGACY_SCHEMA)
            install_v2_schema(conn)
            install_v2_schema(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()[0]
        self.assertEqual(1, count)

    def test_v3_task_card_constraint_is_rebuilt_for_session_cards(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = os.path.join(tmp.name, "memory.db")
            with sqlite3.connect(db_path) as conn:
                conn.executescript(LEGACY_SCHEMA)
                conn.executescript(
                    """
                    CREATE TABLE state_cards (
                      id TEXT PRIMARY KEY, card_key TEXT NOT NULL,
                      kind TEXT NOT NULL, task_id TEXT REFERENCES tasks(id),
                      payload TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
                      current_revision_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                      UNIQUE(card_key,kind), CHECK(kind != 'task' OR task_id IS NOT NULL)
                    );
                    CREATE TABLE state_card_revisions (
                      id TEXT PRIMARY KEY, card_id TEXT, parent_revision_id TEXT, version INTEGER,
                      patch TEXT, before_payload TEXT, after_payload TEXT, actor TEXT, reason TEXT,
                      source_message_id TEXT, tool_trace_id TEXT, subagent_trace_id TEXT,
                      before_hash TEXT, after_hash TEXT, created_at REAL
                    );
                    """
                )
            store = V2Store(db_path)
            store.migrate()
            card = store.put_state_card("session-v4", "task", {"goal": "migrated"})
            self.assertEqual("session-v4", card["session_id"])
            self.assertIsNone(card["task_id"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
