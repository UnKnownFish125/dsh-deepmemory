import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SERVER_DIR)
import server  # noqa: E402


class V2HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        server.DB_PATH = os.path.join(root, "memory.db")
        server.INDEX_PATH = os.path.join(root, "memory.faiss")
        server.BACKUP_DIR = os.path.join(root, "backups")
        self.original_api_token_file = server.API_TOKEN_FILE
        server.API_TOKEN_FILE = os.path.join(root, "api-token")
        os.makedirs(server.BACKUP_DIR)
        server._index = None
        server._search_cache.clear()
        server.init_db()
        with sqlite3.connect(server.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO documents (uuid,content,memory_class,storage_tier,revision_version,status) "
                "VALUES (?,?,?,?,?,?)",
                ("v2-test", "short term deploy note", "short_term", "active", 0, "active"),
            )
            self.memory_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.httpd = server.ThreadingHTTPServer(("localhost", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.base = f"http://localhost:{self.httpd.server_port}"

    def tearDown(self):
        server.API_TOKEN_FILE = self.original_api_token_file
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_bearer_token_required_when_configured(self):
        with open(server.API_TOKEN_FILE, "w", encoding="utf-8") as fh:
            fh.write("test-token")
        self.assertEqual(401, self.request("GET", "/v1/graph")[0])
        self.assertEqual(401, self.request("GET", "/v1/graph", headers={"Authorization": "Bearer wrong"})[0])
        code, _ = self.request("GET", "/v1/graph", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(200, code)

    def test_graph_nodes_include_aggregated_importance(self):
        with sqlite3.connect(server.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO documents (uuid,content,importance,status) VALUES (?,?,?,?)",
                ("graph-high", "important relation", 0.92, "active"),
            )
            memory_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO graph_nodes (name,kind,created_at) VALUES (?,?,?)", ("alpha", "entity", 1))
            source_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO graph_nodes (name,kind,created_at) VALUES (?,?,?)", ("beta", "entity", 1))
            target_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO graph_edges (source_id,target_id,relation,memory_id,created_at) VALUES (?,?,?,?,?)",
                (source_id, target_id, "relates", memory_id, 1),
            )
        code, graph = self.request("GET", "/v1/graph")
        self.assertEqual(200, code)
        nodes = {node["name"]: node for node in graph["nodes"]}
        self.assertAlmostEqual(0.92, nodes["alpha"]["importance"])
        self.assertEqual(1, nodes["alpha"]["memory_count"])
        self.assertEqual(1, nodes["alpha"]["edge_count"])

    def test_task_status_history_and_conflicts(self):
        code, created = self.request("POST", "/v1/v2/tasks", {"title": "ship", "status": "todo", "workspace_id": "ws-1", "session_id": "session-1"})
        self.assertEqual(200, code)
        task = created["task"]

        code, listed = self.request("GET", "/v1/v2/tasks?workspace_id=ws-1")
        self.assertEqual(200, code)
        self.assertEqual([task["id"]], [item["id"] for item in listed["tasks"]])
        self.assertTrue(task["created_at"].endswith("+08:00"))
        code, moved = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/transition",
            {"to_status": "in_progress", "expected_version": 1},
        )
        self.assertEqual(200, code)
        code, _ = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/transition",
            {"to_status": "completed", "expected_version": 1},
        )
        self.assertEqual(409, code)
        code, _ = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/transition",
            {"to_status": "review", "expected_version": moved["task"]["version"]},
        )
        self.assertEqual(200, code)
        review_task = (self.request("GET", f"/v1/v2/tasks/{task['id']}"))[1]["task"]
        code, history = self.request("GET", f"/v1/v2/tasks/{task['id']}/history")
        self.assertEqual(200, code)
        self.assertEqual(["created", "status_changed", "status_changed"], [event["event_type"] for event in history["events"]])
        code, _ = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/transition",
            {"to_status": "completed", "expected_version": review_task["version"]},
        )
        self.assertEqual(200, code)

    def test_task_draft_review_lifecycle_and_limit(self):
        code, created = self.request("POST", "/v1/v2/tasks", {"title": "idea", "status": "draft", "workspace_id": "ws-1", "session_id": "session-1"})
        self.assertEqual(200, code)
        task = created["task"]
        self.assertEqual("draft", task["status"])
        # draft -> planned -> todo -> in_progress -> review -> completed
        for nxt, version in (("planned", task["version"]),):
            code, moved = self.request("POST", f"/v1/v2/tasks/{task['id']}/transition", {"to_status": nxt, "expected_version": version, "reason": "write to conversation"})
            self.assertEqual(200, code)
            version = moved["task"]["version"]
        for nxt in ("todo", "in_progress", "review", "completed"):
            code, moved = self.request("POST", f"/v1/v2/tasks/{task['id']}/transition", {"to_status": nxt, "expected_version": version, "reason": "step"})
            self.assertEqual(200, code)
            version = moved["task"]["version"]
        self.assertEqual("completed", moved["task"]["status"])
        # 数量上限：活跃任务（draft/planned/todo/in_progress/review）超过 max_active_tasks 拒绝
        # draft 不占活跃上限：draft 可随意写
        code, _ = self.request("POST", "/v1/v2/tasks", {"title": "idea1", "status": "draft", "workspace_id": "ws-limit", "session_id": "s1", "max_active_tasks": 2})
        self.assertEqual(200, code)
        code, _ = self.request("POST", "/v1/v2/tasks", {"title": "idea2", "status": "draft", "workspace_id": "ws-limit", "session_id": "s1", "max_active_tasks": 2})
        self.assertEqual(200, code)
        # planned/todo/in_progress/review 计入上限
        code, _ = self.request("POST", "/v1/v2/tasks", {"title": "p1", "status": "planned", "workspace_id": "ws-limit", "session_id": "s1", "max_active_tasks": 2})
        self.assertEqual(200, code)
        code, _ = self.request("POST", "/v1/v2/tasks", {"title": "p2", "status": "planned", "workspace_id": "ws-limit", "session_id": "s1", "max_active_tasks": 2})
        self.assertEqual(200, code)
        code, err = self.request("POST", "/v1/v2/tasks", {"title": "overflow", "status": "planned", "workspace_id": "ws-limit", "session_id": "s1", "max_active_tasks": 2})
        self.assertEqual(400, code)
        self.assertIn("task limit", str(err.get("error", "")))


        code, created = self.request(
            "POST", "/v1/v2/tasks", {"title": "color", "status": "planned", "task_color": "orange", "workspace_id": "ws-1", "session_id": "session-1"}
        )
        self.assertEqual(200, code)
        task = created["task"]
        self.assertEqual("orange", task["task_color"])
        code, updated = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/color",
            {"task_color": "green", "expected_version": task["version"]},
        )
        self.assertEqual(200, code)
        self.assertEqual("green", updated["task"]["task_color"])
        code, _ = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/color",
            {"task_color": "purple", "expected_version": updated["task"]["version"]},
        )
        self.assertEqual(400, code)

    def test_task_delete_soft_removes_from_board(self):
        code, created = self.request("POST", "/v1/v2/tasks", {"title": "del-me", "status": "draft", "workspace_id": "ws-del", "session_id": "s1"})
        self.assertEqual(200, code)
        task = created["task"]
        code, removed = self.request("POST", f"/v1/v2/tasks/{task['id']}/delete", {"reason": "user removed"})
        self.assertEqual(200, code)
        self.assertIsNotNone(removed["task"]["deleted_at"])
        st = server.V2Store(server.DB_PATH)
        self.assertEqual([], st.list_tasks('ws-del'), 'store direct should filter deleted')
        code, listed = self.request("GET", "/v1/v2/tasks?workspace_id=ws-del")
        self.assertEqual(200, code)
        self.assertEqual([], listed["tasks"])
        code, _ = self.request("POST", f"/v1/v2/tasks/{task['id']}/delete", {})
        self.assertEqual(409, code)

    def test_task_sol_advice_roundtrip(self):
        code, created = self.request("POST", "/v1/v2/tasks", {"title": "fail-me", "status": "todo", "workspace_id": "ws-sol", "session_id": "s1"})
        self.assertEqual(200, code)
        task = created["task"]
        code, moved = self.request("POST", f"/v1/v2/tasks/{task['id']}/transition", {"to_status": "in_progress", "expected_version": task["version"], "reason": "go"})
        task = moved["task"]
        code, failed = self.request("POST", f"/v1/v2/tasks/{task['id']}/transition", {"to_status": "failed", "expected_version": task["version"], "reason": "外援失败"})
        task = failed["task"]
        code, adv = self.request("POST", f"/v1/v2/tasks/{task['id']}/sol-advice", {"advice": "- 换路线\n- 降级", "expected_version": task["version"]})
        self.assertEqual(200, code)
        self.assertIn("换路线", adv["task"]["sol_advice"])
        code, _ = self.request("POST", f"/v1/v2/tasks/{task['id']}/sol-advice", {"advice": "x", "expected_version": task["version"]})
        self.assertEqual(409, code)

    def test_task_binding_endpoint_and_workspace_requirement(self):
        code, response = self.request("POST", "/v1/v2/tasks", {"title": "missing binding"})
        self.assertEqual(400, code)
        code, created = self.request("POST", "/v1/v2/tasks", {
            "title": "bound", "workspace_id": "ws-1", "session_id": "session-1",
        })
        self.assertEqual(200, code)
        task = created["task"]
        code, rebound = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/binding",
            {"workspace_id": "ws-1", "session_id": "session-2", "expected_version": task["version"]},
        )
        self.assertEqual(200, code)
        self.assertEqual("session-2", rebound["task"]["session_id"])

    def test_state_card_revisions_restore_and_conflict(self):
        code, first = self.request("PUT", "/v1/v2/cards/task/session-1", {"payload": {"goal": "a"}})
        self.assertEqual(200, code)
        self.assertEqual("session-1", first["card"]["session_id"])
        self.assertIsNone(first["card"]["task_id"])
        code, second = self.request(
            "PUT", "/v1/v2/cards/task/session-1",
            {"payload": {"goal": "b"}, "expected_version": first["card"]["version"]},
        )
        self.assertEqual(200, code)
        code, _ = self.request(
            "PUT", "/v1/v2/cards/task/session-1",
            {"payload": {"goal": "overwrite"}, "expected_version": 1},
        )
        self.assertEqual(409, code)
        code, revisions = self.request("GET", "/v1/v2/cards/task/session-1/revisions")
        self.assertEqual(200, code)
        self.assertEqual(2, len(revisions["revisions"]))
        revision_id = revisions["revisions"][-1]["id"]
        code, restored = self.request(
            "POST", "/v1/v2/cards/task/session-1/restore",
            {"revision_id": revision_id, "expected_version": second["card"]["version"]},
        )
        self.assertEqual(200, code)
        self.assertEqual("a", restored["card"]["payload"]["goal"])

    def test_browser_origin_cannot_access_loopback_api(self):
        code, response = self.request(
            "GET", "/v1/config", headers={"Origin": "https://attacker.example"}
        )
        self.assertEqual(403, code)
        self.assertEqual("browser origin is not allowed", response["error"])

    def test_config_read_redacts_secrets(self):
        code, _ = self.request("POST", "/v1/config", {
            "embedding.api_key": "sk-" + "test-secret-value-1234567890",
            "embedding.provider": "api",
        })
        self.assertEqual(200, code)
        code, response = self.request("GET", "/v1/config")
        self.assertEqual(200, code)
        self.assertNotIn("embedding.api_key", response["config"])
        code, response = self.request("GET", "/v1/settings/deepmemory.embedding.api_key")
        self.assertEqual(404, code)
        self.assertNotIn("sk-test-secret", json.dumps(response))

    def test_oversized_request_is_rejected(self):
        old_limit = server.MAX_BODY_BYTES
        server.MAX_BODY_BYTES = 32
        try:
            code, response = self.request("POST", "/v1/config", {"padding": "x" * 64})
        finally:
            server.MAX_BODY_BYTES = old_limit
        self.assertEqual(413, code)
        self.assertEqual("request body too large", response["error"])

    def test_private_embedding_target_is_rejected(self):
        code, _ = self.request("POST", "/v1/config", {
            "embedding.provider": "api",
            "embedding.api_base_url": "http://" + "127.0.0.1" + ":9999/v1",
            "embedding.api_key": "sk-" + "test-only",
        })
        self.assertEqual(200, code)
        code, response = self.request("POST", "/v1/embeddings", {"input": ["hello"]})
        self.assertEqual(400, code)
        self.assertIn("HTTPS public endpoint", response["error"])

    def test_lifecycle_dispute_recall_and_illegal_transition(self):
        code, moved = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/migrate",
            {"target_tier": "cold", "expected_version": 0},
        )
        self.assertEqual(200, code)
        self.assertEqual("cold", moved["memory"]["storage_tier"])
        code, recall = self.request(
            "POST", "/v1/v2/recall",
            {"query": "deploy", "expand_to": "cold"},
        )
        self.assertEqual(200, code)
        self.assertEqual(["active", "cold"], [group["tier"] for group in recall["tiers"]])
        self.assertTrue(recall["results"])
        code, _ = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/migrate",
            {"target_tier": "active", "expected_version": 1},
        )
        self.assertEqual(200, code)
        code, marked = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/dispute",
            {"expected_version": 2, "reason": "conflicting evidence"},
        )
        self.assertEqual(200, code)
        self.assertEqual(3, marked["revision_version"])
        code, resolved = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/dispute",
            {"action": "update", "expected_version": 3, "actor": "user", "changes": {"content": "updated"}},
        )
        self.assertEqual(200, code)
        self.assertEqual(4, resolved["revision_version"])
        code, _ = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/dispute",
            {"action": "update", "expected_version": 4, "actor": "user", "changes": {"unknown": "bad"}},
        )
        self.assertEqual(400, code)
        code, _ = self.request(
            "POST", f"/v1/v2/memories/{self.memory_id}/migrate",
            {"target_tier": "archive", "expected_version": 4},
        )
        self.assertEqual(400, code)


class SessionKeyMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        server.DB_PATH = os.path.join(root, "memory.db")
        server.INDEX_PATH = os.path.join(root, "memory.faiss")
        server.BACKUP_DIR = os.path.join(root, "backups")
        self.original_api_token_file = server.API_TOKEN_FILE
        server.API_TOKEN_FILE = os.path.join(root, "api-token")
        os.makedirs(server.BACKUP_DIR)
        server._index = None
        server._search_cache.clear()
        server.init_db()
        self.httpd = server.ThreadingHTTPServer(("localhost", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()
        self.base = f"http://localhost:{self.httpd.server_port}"

    def tearDown(self):
        server.API_TOKEN_FILE = self.original_api_token_file
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_session_key_create_rotate_revoke(self):
        sid = "session-key-test"
        code, created = self.request("POST", "/v1/v2/session-keys", {"workspace_id": "ws1", "session_id": sid})
        self.assertEqual(200, code)
        full = created["key"]["key"]
        self.assertTrue(full.startswith("dmk."))
        self.assertTrue(created["key"]["prefix"].startswith("dmk."))
        # descriptor 不泄露全 key
        code, desc = self.request("GET", f"/v1/v2/session-keys/{sid}?workspace_id=ws1")
        self.assertEqual(200, code)
        self.assertNotIn("key", desc["key"])
        # rotate 后旧 key 失效、新 key 可用
        code, rotated = self.request("POST", f"/v1/v2/session-keys/{sid}/rotate", {"workspace_id": "ws1"})
        self.assertEqual(200, code)
        new_full = rotated["key"]["key"]
        self.assertNotEqual(full, new_full)
        code, blocked = self.request("GET", f"/v1/v2/sessions/{sid}/memories/export?key={full}")
        self.assertEqual(403, code)

    def test_session_export_import_purge(self):
        source, target = "session-export-src", "session-import-dst"
        code, _ = self.request("POST", "/v1/memories/add", {
            "content": "待迁移事实", "type": "fact", "domain": "work",
            "scope": "session", "session_id": source, "workspace_id": "ws1",
        })
        self.assertEqual(200, code)
        code, exp = self.request("GET", f"/v1/v2/sessions/{source}/memories/export")
        self.assertEqual(200, code)
        self.assertEqual(1, len(exp["memories"]))
        self.assertEqual("待迁移事实", exp["memories"][0]["content"])
        code, imported = self.request("POST", f"/v1/v2/sessions/{target}/memories/import", {"payload": exp, "mode": "merge"})
        self.assertEqual(200, code)
        self.assertEqual(1, imported["inserted"])
        with sqlite3.connect(server.DB_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM documents WHERE session_id=? AND status='active'", (target,)).fetchone()[0]
        self.assertEqual(1, n)
        code, purged = self.request("POST", f"/v1/v2/sessions/{target}/purge", {})
        self.assertEqual(200, code)
        self.assertEqual(1, purged["documents"])
        with sqlite3.connect(server.DB_PATH) as conn:
            archived = conn.execute("SELECT COUNT(*) FROM documents WHERE session_id=? AND status='archived'", (target,)).fetchone()[0]
        self.assertEqual(1, archived)


if __name__ == "__main__":
    unittest.main()
