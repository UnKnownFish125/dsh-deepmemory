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
        code, created = self.request("POST", "/v1/v2/tasks", {"title": "ship", "status": "todo"})
        self.assertEqual(200, code)
        task = created["task"]
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
        code, history = self.request("GET", f"/v1/v2/tasks/{task['id']}/history")
        self.assertEqual(200, code)
        self.assertEqual(["created", "status_changed"], [event["event_type"] for event in history["events"]])
        code, _ = self.request(
            "POST", f"/v1/v2/tasks/{task['id']}/transition",
            {"to_status": "completed", "expected_version": moved["task"]["version"]},
        )
        self.assertEqual(200, code)

    def test_task_color_create_and_update(self):
        code, created = self.request(
            "POST", "/v1/v2/tasks", {"title": "color", "status": "planned", "task_color": "orange"}
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

    def test_state_card_revisions_restore_and_conflict(self):
        code, first = self.request("PUT", "/v1/v2/cards/daily/session-1", {"payload": {"goal": "a"}})
        self.assertEqual(200, code)
        code, second = self.request(
            "PUT", "/v1/v2/cards/daily/session-1",
            {"payload": {"goal": "b"}, "expected_version": first["card"]["version"]},
        )
        self.assertEqual(200, code)
        code, _ = self.request(
            "PUT", "/v1/v2/cards/daily/session-1",
            {"payload": {"goal": "overwrite"}, "expected_version": 1},
        )
        self.assertEqual(409, code)
        code, revisions = self.request("GET", "/v1/v2/cards/daily/session-1/revisions")
        self.assertEqual(200, code)
        self.assertEqual(2, len(revisions["revisions"]))
        revision_id = revisions["revisions"][-1]["id"]
        code, restored = self.request(
            "POST", "/v1/v2/cards/daily/session-1/restore",
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


if __name__ == "__main__":
    unittest.main()
