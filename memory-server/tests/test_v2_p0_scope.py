"""P0 regression tests: v2 workspace scoping, unified redacting write gate.

Covers the audited defects:
  * export / import / purge must be keyed by (workspace_id, session_id), so two
    workspaces sharing one session_id cannot read or purge each other's store;
  * lifecycle updates, state cards and imports must redact sensitive literals
    (they used to write raw content / payload / revision payloads);
  * imports must keep long content (the old code silently cut it to 500 chars);
  * exports must never carry an unredacted state card when include_sensitive is
    false;
  * GET /v1/overview must really filter by workspace_id and session_id.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER_DIR)

from sensitive import redact_text  # noqa: E402
from v2_domain import (  # noqa: E402
    DomainError,
    NotFoundError,
    PermissionDenied,
    V2Store,
)


SECRET = "sk-" + "abcdefghijklmnopqrstuvwxyz012345"


def _redacted(value):
    return redact_text(value)[0]


DOCUMENTS_SCHEMA = """
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid TEXT UNIQUE NOT NULL,
  content TEXT NOT NULL,
  key_facts TEXT DEFAULT '',
  persona_summary TEXT DEFAULT '',
  canonical_summary TEXT DEFAULT '',
  type TEXT DEFAULT 'fact',
  domain TEXT DEFAULT 'work',
  scope TEXT DEFAULT 'session',
  workspace_id TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  persona_id TEXT DEFAULT '',
  importance REAL DEFAULT 0.5,
  created_at REAL,
  last_access_at REAL,
  access_count INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  keywords TEXT DEFAULT '',
  has_sensitive INTEGER DEFAULT 0,
  sensitive_types TEXT DEFAULT '[]'
);
CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at REAL
);
CREATE TABLE protected_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER,
  content TEXT
);
CREATE TABLE atoms (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER, status TEXT DEFAULT 'active');
CREATE TABLE graph_nodes (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE graph_edges (id INTEGER PRIMARY KEY, memory_id INTEGER);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""


class ScopedStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory.db")
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(DOCUMENTS_SCHEMA)
        self.store = V2Store(self.db_path)
        self.store.migrate()

    def tearDown(self):
        self.tmp.cleanup()

    def insert_document(
        self,
        content,
        workspace_id="",
        session_id="",
        scope="session",
        status="active",
        has_sensitive=0,
    ):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO documents (uuid,content,workspace_id,session_id,scope,status,"
                "has_sensitive,revision_version) VALUES (?,?,?,?,?,?,?,0)",
                (
                    "uuid-" + str(abs(hash((content, workspace_id, session_id)))),
                    content,
                    workspace_id,
                    session_id,
                    scope,
                    status,
                    has_sensitive,
                ),
            )
            return cur.lastrowid

    def fetch(self, sql, args=()):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, args).fetchall()

    # ---------------------------------------------------------------- export / purge

    def test_export_is_workspace_scoped_for_shared_session_id(self):
        self.insert_document("ws-1 note", workspace_id="ws-1", session_id="s-shared")
        self.insert_document("ws-2 note", workspace_id="ws-2", session_id="s-shared")
        self.insert_document("archived note", workspace_id="ws-1", session_id="s-shared",
                             status="archived")
        first = self.store.export_session("ws-1", "s-shared")
        second = self.store.export_session("ws-2", "s-shared")
        self.assertEqual(["ws-1 note"], [m["content"] for m in first["memories"]])
        self.assertEqual(["ws-2 note"], [m["content"] for m in second["memories"]])
        self.assertEqual("ws-1", first["workspace_id"])
        self.assertEqual("ws-2", second["workspace_id"])
        # 旧调用（无 workspace 信息）保持原有的"仅按 session"兼容语义，但不得跨 workspace
        # 泄露到显式带 workspace 的调用里（上一组断言已覆盖）。
        legacy = self.store.export_session("", "s-shared")
        self.assertEqual(2, len(legacy["memories"]))

    def test_purge_is_workspace_scoped_for_shared_session_id(self):
        mine = self.insert_document("ws-1 keep", workspace_id="ws-1", session_id="s-shared")
        other = self.insert_document("ws-2 survive", workspace_id="ws-2", session_id="s-shared")
        result = self.store.purge_session("s-shared", workspace_id="ws-1")
        self.assertEqual(1, result["documents"])
        self.assertEqual("ws-1", result["workspace_id"])
        rows = {r["id"]: r["status"] for r in self.fetch("SELECT id,status FROM documents")}
        self.assertEqual("archived", rows[mine])
        self.assertEqual("active", rows[other])

    def test_hard_purge_is_workspace_scoped_for_shared_session_id(self):
        mine = self.insert_document("ws-1 hard", workspace_id="ws-1", session_id="s-hard")
        other = self.insert_document("ws-2 hard", workspace_id="ws-2", session_id="s-hard")
        result = self.store.purge_session("s-hard", hard=True, workspace_id="ws-1")
        self.assertEqual(1, result["documents"])
        ids = {r["id"] for r in self.fetch("SELECT id FROM documents")}
        self.assertNotIn(mine, ids)
        self.assertIn(other, ids)

    # ---------------------------------------------------------------- redaction

    def test_lifecycle_update_redacts_content_and_revisions(self):
        memory_id = self.insert_document("old note", workspace_id="ws-1", session_id="s-1")
        version = self.store.update_memory_lifecycle(
            memory_id,
            {"content": "deploy token " + SECRET, "key_facts": "key " + SECRET},
            expected_version=0,
            actor="main_agent",
            reason="test redaction",
        )
        self.assertEqual(1, version)
        row = self.fetch("SELECT content,key_facts,has_sensitive,sensitive_types FROM documents")[0]
        self.assertNotIn(SECRET, row["content"])
        self.assertNotIn(SECRET, row["key_facts"])
        self.assertIn("[REDACTED:api_key]", row["content"])
        self.assertEqual(1, row["has_sensitive"])
        self.assertIn("api_key", json.loads(row["sensitive_types"]))
        revisions = self.fetch("SELECT patch,before_payload,after_payload FROM memory_revisions")
        self.assertEqual(1, len(revisions))
        for value in revisions[0]:
            self.assertNotIn(SECRET, value)

    def test_lifecycle_update_rejects_cross_workspace_write(self):
        memory_id = self.insert_document("ws-1 owned", workspace_id="ws-1", session_id="s-1")
        with self.assertRaises(PermissionDenied):
            self.store.update_memory_lifecycle(
                memory_id, {"content": "hijack"}, expected_version=0,
                actor="main_agent", workspace_id="ws-2",
            )
        # 同一 workspace 的写入照常通过
        version = self.store.update_memory_lifecycle(
            memory_id, {"content": "updated"}, expected_version=0,
            actor="main_agent", workspace_id="ws-1",
        )
        self.assertEqual(1, version)
        row = self.fetch("SELECT workspace_id,content FROM documents")[0]
        self.assertEqual(("ws-1", "updated"), (row["workspace_id"], row["content"]))

    def test_state_card_payload_is_redacted_and_workspace_scoped(self):
        first = self.store.put_state_card(
            "s-shared", "daily", {"goal": "rotate token " + SECRET}, workspace_id="ws-1"
        )
        second = self.store.put_state_card(
            "s-shared", "daily", {"goal": "ws-2 goal"}, workspace_id="ws-2"
        )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(1, first["version"])
        self.assertEqual(1, second["version"])
        self.assertNotIn(SECRET, json.dumps(first["payload"], ensure_ascii=False))
        self.assertIn("[REDACTED:api_key]", first["payload"]["goal"])
        ws1_card = self.store.get_state_card("s-shared", "daily", workspace_id="ws-1")
        ws2_card = self.store.get_state_card("s-shared", "daily", workspace_id="ws-2")
        self.assertEqual(_redacted("rotate token " + SECRET), ws1_card["payload"]["goal"])
        self.assertEqual("ws-2 goal", ws2_card["payload"]["goal"])
        payloads = self.fetch("SELECT payload FROM state_cards ORDER BY workspace_id")
        self.assertEqual(2, len(payloads))
        for row in payloads:
            self.assertNotIn(SECRET, row["payload"])
        revisions = self.fetch("SELECT patch,before_payload,after_payload FROM state_card_revisions")
        self.assertEqual(2, len(revisions))
        for revision in revisions:
            for value in revision:
                self.assertNotIn(SECRET, value)

    def test_state_card_legacy_unbound_row_is_adopted_not_duplicated(self):
        card = self.store.put_state_card("legacy-session", "task", {"goal": "legacy"})
        self.assertEqual("", card["workspace_id"])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO session_keys (id,workspace_id,session_id,key_hash,created_at) "
                         "VALUES ('k1','ws-1','legacy-session','hash-1',0)")
        scoped = self.store.put_state_card(
            "legacy-session", "task", {"goal": "legacy scoped"}, expected_version=card["version"],
            workspace_id="ws-1",
        )
        self.assertEqual(card["id"], scoped["id"])
        self.assertEqual("ws-1", scoped["workspace_id"])
        rows = self.fetch("SELECT id FROM state_cards")
        self.assertEqual(1, len(rows))

    # ---------------------------------------------------------------- import

    def test_import_keeps_full_900_char_content(self):
        long_content = "长" * 900
        payload = {
            "format": "deepmemory-session-v1",
            "workspace_id": "ws-1",
            "session_id": "s-src",
            "memories": [{
                "content": long_content,
                "scope": "session",
                "type": "fact",
                "domain": "work",
                "importance": 0.6,
            }],
        }
        result = self.store.import_session_memories(
            "s-dst", payload, mode="merge", workspace_id="ws-1"
        )
        self.assertEqual(1, result["inserted"])
        self.assertEqual(0, result["truncated"])
        self.assertIsNone(result["max_content_chars"])
        row = self.fetch(
            "SELECT content,workspace_id,session_id,revision_version FROM documents"
        )[0]
        self.assertEqual(900, len(row["content"]))
        self.assertEqual(long_content, row["content"])
        self.assertEqual("ws-1", row["workspace_id"])
        self.assertEqual("s-dst", row["session_id"])
        self.assertEqual(1, row["revision_version"])
        # merge 去重仍然生效（不会因为去掉截断而重复插入）
        again = self.store.import_session_memories(
            "s-dst", payload, mode="merge", workspace_id="ws-1"
        )
        self.assertEqual(1, again["merged"])
        self.assertEqual(0, again["inserted"])

    def test_import_redacts_and_scopes_to_target_workspace(self):
        payload = {
            "format": "deepmemory-session-v1",
            "workspace_id": "ws-1",
            "memories": [{
                "content": "migrated token " + SECRET,
                "key_facts": "facts " + SECRET,
                "scope": "session",
            }],
            "card": {
                "kind": "daily",
                "version": 1,
                "payload": {"goal": "card token " + SECRET},
            },
        }
        result = self.store.import_session_memories(
            "s-dst", payload, mode="merge", workspace_id="ws-2"
        )
        self.assertEqual(1, result["inserted"])
        self.assertTrue(result["card_imported"])
        row = self.fetch("SELECT content,key_facts,workspace_id,has_sensitive FROM documents")[0]
        self.assertNotIn(SECRET, row["content"] + row["key_facts"])
        self.assertEqual("ws-2", row["workspace_id"])
        self.assertEqual(1, row["has_sensitive"])
        card = self.store.get_state_card("s-dst", "daily", workspace_id="ws-2")
        self.assertNotIn(SECRET, json.dumps(card["payload"], ensure_ascii=False))
        # 源 workspace 里查不到导入结果（隔离）
        self.assertEqual([], self.store.export_session("ws-1", "s-dst")["memories"])
        self.assertEqual(1, len(self.store.export_session("ws-2", "s-dst")["memories"]))

    def test_import_legacy_card_shape_is_normalized(self):
        # v1 导出件把 card.payload 序列化成字符串；导入必须解析后存成对象。
        payload = {
            "memories": [{"content": "legacy card bundle", "scope": "session"}],
            "card": {"payload": json.dumps({"goal": "legacy card"}), "version": 3},
        }
        result = self.store.import_session_memories("s-dst", payload)
        self.assertTrue(result["card_imported"])
        card = self.store.get_state_card("s-dst", "daily")
        self.assertEqual({"goal": "legacy card"}, card["payload"])

    # ---------------------------------------------------------------- export redaction

    def test_export_redacts_state_card_and_legacy_content(self):
        raw_card = json.dumps({"goal": "card token " + SECRET}, ensure_ascii=False)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO state_cards (id,card_key,workspace_id,session_id,kind,payload,"
                "version,created_at,updated_at) VALUES (?,?,?,?,?,?,1,0,0)",
                ("card-legacy", "s-export", "ws-1", "s-export", "daily", raw_card),
            )
        # 历史遗留行里仍可能残留明文（写入收口之前落库的）
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO documents (uuid,content,workspace_id,session_id,scope,status,"
                "has_sensitive,revision_version) VALUES (?,?,?,?,?,?,1,0)",
                ("legacy-raw", "raw token " + SECRET, "ws-1", "s-export", "session", "active"),
            )
        export = self.store.export_session("ws-1", "s-export", include_sensitive=False)
        self.assertIsNotNone(export["card"])
        self.assertNotIn(SECRET, json.dumps(export, ensure_ascii=False))
        self.assertIn("[REDACTED:api_key]", export["card"]["payload"]["goal"])
        self.assertEqual(_redacted("raw token " + SECRET), export["memories"][0]["content"])
        self.assertEqual(0, export["truncated"])
        authorized = self.store.export_session("ws-1", "s-export", include_sensitive=True)
        self.assertIn(SECRET, json.dumps(authorized, ensure_ascii=False))

    def test_recall_is_workspace_scoped(self):
        self.insert_document("deploy note", workspace_id="ws-1", session_id="s-1",
                             scope="workspace")
        self.insert_document("deploy note", workspace_id="ws-2", session_id="s-2",
                             scope="workspace")
        scoped = self.store.recall("deploy", workspace_id="ws-1")
        self.assertEqual(["ws-1"], [item["workspace_id"] for item in scoped["results"]])
        legacy = self.store.recall("deploy")
        self.assertEqual(2, len(legacy["results"]))

    # ---------------------------------------------------------------- write gate

    def test_write_memory_runs_redaction_revisions_and_hooks(self):
        events = []
        self.store.set_write_hooks(
            index_hook=lambda event: events.append(("index", event)),
            cache_hook=lambda event: events.append(("cache", event)),
        )
        result = self.store.write_memory(
            {"content": "fresh token " + SECRET, "scope": "session", "type": "fact"},
            workspace_id="ws-1",
            session_id="s-hook",
        )
        self.assertTrue(result["inserted"])
        self.assertEqual(1, result["revision_version"])
        self.assertEqual(["index", "cache"], [kind for kind, _ in events])
        self.assertEqual("insert", events[0][1]["action"])
        row = self.fetch("SELECT content,revision_version FROM documents")[0]
        self.assertNotIn(SECRET, row["content"])
        revision = self.fetch("SELECT after_payload FROM memory_revisions")[0]
        self.assertNotIn(SECRET, revision["after_payload"])

    def test_write_memory_validates_scope(self):
        with self.assertRaises(DomainError):
            self.store.write_memory(
                {"content": "no session", "scope": "session"}, workspace_id="ws-1"
            )
        with self.assertRaises(DomainError):
            self.store.write_memory(
                {"content": "unknown scope", "scope": "somewhere"}, session_id="s-1"
            )
        with self.assertRaises(NotFoundError):
            self.store.write_memory({"content": "x"}, session_id="s-1", memory_id=999,
                                    expected_version=0)
        with self.assertRaises(DomainError):
            self.store.write_memory(
                {"content": "x", "bad_field": True}, workspace_id="ws-1", session_id="s-1"
            )


class OverviewScopeTest(unittest.TestCase):
    def setUp(self):
        import server

        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.server.DB_PATH = os.path.join(root, "memory.db")
        self.server.INDEX_PATH = os.path.join(root, "memory.faiss")
        self.server.BACKUP_DIR = os.path.join(root, "backups")
        self.server.API_TOKEN_FILE = os.path.join(root, "api-token")
        os.makedirs(self.server.BACKUP_DIR)
        self.server._index = None
        self.server._search_cache.clear()
        self.server.init_db()
        with sqlite3.connect(self.server.DB_PATH) as conn:
            rows = [
                ("ws-1 session", "ws-1", "s-shared", "session"),
                ("ws-2 session", "ws-2", "s-shared", "session"),
                ("ws-1 workspace", "ws-1", "s-other", "workspace"),
                ("global memory", "ws-2", "", "global"),
                ("legacy unbound session", "", "s-legacy", "session"),
            ]
            for index, (content, workspace_id, session_id, scope) in enumerate(rows):
                conn.execute(
                    "INSERT INTO documents (uuid,content,workspace_id,session_id,scope,status) "
                    "VALUES (?,?,?,?,?,'active')",
                    ("overview-%d" % index, content, workspace_id, session_id, scope),
                )

    def tearDown(self):
        self.tmp.cleanup()

    def contents(self, workspace_id=None, session_id=None):
        overview = self.server.get_overview(
            workspace_id=workspace_id, session_id=session_id
        )
        return sorted(item["content"] for item in overview["memories"])

    def test_overview_filters_by_workspace_and_session(self):
        # 同一 session_id 在 ws-1 / ws-2 各有记忆：只返回本 workspace 的 + global
        self.assertEqual(
            ["global memory", "ws-1 session", "ws-1 workspace"],
            self.contents("ws-1", "s-shared"),
        )
        self.assertEqual(
            ["global memory", "ws-2 session"],
            self.contents("ws-2", "s-shared"),
        )
        # workspace 维度（无 session）只看到该 workspace + global
        self.assertEqual(
            ["global memory", "ws-1 session", "ws-1 workspace"],
            self.contents("ws-1"),
        )
        # 历史无归属（workspace_id='')的 session 记忆：按同一 session_id 请求时仍可见
        # （workspace 级记忆在该 workspace 的任一 session 视图都可见）
        self.assertEqual(
            ["global memory", "legacy unbound session", "ws-1 workspace"],
            self.contents("ws-1", "s-legacy"),
        )
        # 两个参数都不给的旧调用保持全量语义
        self.assertEqual(
            ["global memory", "legacy unbound session", "ws-1 session",
             "ws-1 workspace", "ws-2 session"],
            self.contents(),
        )

    def test_overview_counts_follow_scope(self):
        scoped = self.server.get_overview(workspace_id="ws-1", session_id="s-shared")
        legacy = self.server.get_overview()
        self.assertEqual(3, scoped["documents"])
        self.assertEqual(5, legacy["documents"])
        self.assertEqual("ws-1", scoped["workspace_id"])
        self.assertEqual("s-shared", scoped["session_id"])


class HttpScopeTest(unittest.TestCase):
    """HTTP layer: v2 session routes must carry the workspace scope."""

    def setUp(self):
        import threading

        import server

        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.server.DB_PATH = os.path.join(root, "memory.db")
        self.server.INDEX_PATH = os.path.join(root, "memory.faiss")
        self.server.BACKUP_DIR = os.path.join(root, "backups")
        self.server.API_TOKEN_FILE = os.path.join(root, "api-token")
        os.makedirs(self.server.BACKUP_DIR)
        self.server._index = None
        self.server._search_cache.clear()
        self.server.init_db()
        with sqlite3.connect(self.server.DB_PATH) as conn:
            for index, (workspace_id, content) in enumerate(
                (("ws-1", "ws-1 http note"), ("ws-2", "ws-2 http note"))
            ):
                conn.execute(
                    "INSERT INTO documents (uuid,content,workspace_id,session_id,scope,status) "
                    "VALUES (?,?,?,?,'session','active')",
                    ("http-%d" % index, content, workspace_id, "s-http"),
                )
        self.httpd = self.server.ThreadingHTTPServer(("localhost", 0), self.server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.base = "http://localhost:%d" % self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None):
        import urllib.error
        import urllib.request

        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_export_and_purge_honor_requested_workspace(self):
        code, exported = self.request(
            "GET", "/v1/v2/sessions/s-http/memories/export?workspace_id=ws-1"
        )
        self.assertEqual(200, code)
        self.assertEqual(["ws-1 http note"], [m["content"] for m in exported["memories"]])
        code, exported2 = self.request(
            "GET", "/v1/v2/sessions/s-http/memories/export?workspace_id=ws-2"
        )
        self.assertEqual(["ws-2 http note"], [m["content"] for m in exported2["memories"]])
        code, purged = self.request(
            "POST", "/v1/v2/sessions/s-http/purge", {"workspace_id": "ws-1"}
        )
        self.assertEqual(200, code)
        self.assertEqual(1, purged["documents"])
        self.assertEqual("ws-1", purged["workspace_id"])
        with sqlite3.connect(self.server.DB_PATH) as conn:
            rows = {
                row[0]: row[1]
                for row in conn.execute("SELECT workspace_id,status FROM documents")
            }
        self.assertEqual("archived", rows["ws-1"])
        self.assertEqual("active", rows["ws-2"])

    def test_session_key_resolves_workspace_scope(self):
        code, created = self.request(
            "POST", "/v1/v2/session-keys", {"workspace_id": "ws-1", "session_id": "s-keyed"}
        )
        self.assertEqual(200, code)
        key = created["key"]["key"]
        with sqlite3.connect(self.server.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO documents (uuid,content,workspace_id,session_id,scope,status) "
                "VALUES ('keyed-1','keyed note','ws-1','s-keyed','session','active')"
            )
        code, exported = self.request(
            "GET", "/v1/v2/sessions/s-keyed/memories/export?key=" + key
        )
        self.assertEqual(200, code)
        self.assertEqual("ws-1", exported["workspace_id"])
        self.assertEqual(["keyed note"], [m["content"] for m in exported["memories"]])
        # key 只属于 ws-1：拿它去导 ws-1 之外的 session 必须被拒
        code, _ = self.request(
            "GET", "/v1/v2/sessions/s-http/memories/export?key=" + key
        )
        self.assertEqual(403, code)


if __name__ == "__main__":
    unittest.main()
