"""deepmemory 知识库模型契约单测（contract-lib v0.2）：
G1 检索过滤 storage_tier 三路径 / G2 library 分库与校验 / G3 库级归档与恢复 /
G5 document_links 双向溯源 / bias 约束 / 既有路由保留。
"""
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
from v2_domain import V2Store, InvalidTransition, PermissionDenied  # noqa: E402


class LibraryModelTest(unittest.TestCase):
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
        self.store = V2Store(server.DB_PATH)
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
            self.base + path, data=data, method=method, headers=request_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def add(self, content, **kw):
        body = {
            "content": content,
            "workspace_id": "ws1",
            "session_id": "s1",
            "scope": kw.pop("scope", "workspace"),
            "importance": kw.pop("importance", 0.6),
        }
        body.update(kw)
        r = server.add_memory(body)
        return int(r["id"])

    # ---------------- G2: library 列与校验 ----------------

    def test_library_column_and_triggers(self):
        with sqlite3.connect(server.DB_PATH) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)")]
            self.assertIn("library", cols)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            self.assertIn("document_links", tables)
            # UPDATE 触发器：重分类进 bias 且 scope != global 必须拒绝
            conn.execute(
                "INSERT INTO documents (uuid,content,library,scope,importance,created_at,status,keywords,"
                "has_sensitive,sensitive_types,topic_id,event_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("u1", "x", "project", "workspace", 0.6, 1, "active", "", 0, "[]", "", 1),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE documents SET library='bias' WHERE content='x'")
            conn.execute("UPDATE documents SET library='core' WHERE content='x'")
            self.assertEqual("core", conn.execute("SELECT library FROM documents WHERE content='x'").fetchone()[0])

    def test_bias_constraints_on_add(self):
        self.add("正常记忆", library="project")
        with self.assertRaises(ValueError):
            self.add("约束但 scope 非 global", library="bias", scope="workspace", importance=0.9)
        with self.assertRaises(ValueError):
            self.add("约束但 importance 低", library="bias", scope="global", importance=0.5)
        self.add("必须先测试机验证", library="bias", scope="global", importance=0.95)
        with sqlite3.connect(server.DB_PATH) as conn:
            n = conn.execute("SELECT COUNT(*) FROM documents WHERE library='bias'").fetchone()[0]
            self.assertEqual(1, n)

    # ---------------- G2: library 分库检索 ----------------

    def test_library_filter_in_search(self):
        self.add("架构决策：workspace 硬过滤", library="core", topic_id="架构")
        self.add("任务看板四栏实现完成", library="project", topic_id="任务看板")
        core = server.search_memories("架构", k=10, workspace_id="ws1", library="core")
        self.assertTrue(core)
        self.assertTrue(all(r["library"] == "core" for r in core))
        all_res = server.search_memories("架构", k=10, workspace_id="ws1")
        self.assertTrue(any(r["library"] == "project" for r in all_res))

    # ---------------- G1: archive tier 检索过滤三路径 ----------------

    def test_archive_excluded_from_recall_and_graph(self):
        mid = self.add("将被归档的独特记忆 unique-archive-xyz", library="project", topic_id="任务看板")
        self.store.update_memory_lifecycle(
            mid, {"storage_tier": "archive", "memory_class": "compressed_archive"}, 0,
            actor="main_agent", reason="test")
        hidden = server.search_memories("unique-archive-xyz", k=10, workspace_id="ws1")
        self.assertTrue(all(r["id"] != mid for r in hidden), "archive leaked into recall")
        server._search_cache.clear()
        shown = server.search_memories("unique-archive-xyz", k=10, workspace_id="ws1", include_archived=True)
        self.assertTrue(any(r["id"] == mid for r in shown), "include_archived missing")
        gres = server.graph_search("unique-archive-xyz", 10)
        self.assertTrue(all(i != mid for i, _ in gres), "archive leaked into graph")

    # ---------------- G3: 库级归档 + restore-tier ----------------

    def test_archive_library_and_restore_tier(self):
        # 用 SQL 直插两条（绕过 add_memory 的近义合并，专注验证批量归档）
        now = 1000.0
        with sqlite3.connect(server.DB_PATH) as conn:
            for i, c in enumerate(["任务看板看板栏完成 alpha", "任务看板草稿转正式流程 beta"]):
                conn.execute(
                    "INSERT INTO documents (uuid,content,library,topic_id,scope,importance,created_at,"
                    "status,keywords,has_sensitive,sensitive_types,event_time,workspace_id,session_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"u-{i}", c, "project", "任务看板", "workspace", 0.6, now, "active", "", 0, "[]", now, "ws1", "s1"))
        self.add("核心契约决策", library="core", topic_id="契约")
        res = self.store.archive_library("project", topic="任务看板", actor="main_agent", reason="项目结束")
        self.assertEqual(2, res["archived"])
        with self.assertRaises(PermissionDenied):
            self.store.archive_library("bias")
        # 归档后默认不召回；restore-tier 恢复
        mid = res["ids"][0]
        restored = self.store.migrate_memory(mid, "active", 0, actor="main_agent", reason="restore")
        self.assertEqual("active", restored["storage_tier"])
        self.assertEqual("semantic", restored["memory_class"])
        with sqlite3.connect(server.DB_PATH) as conn:
            tier = conn.execute("SELECT storage_tier FROM documents WHERE id=?", (mid,)).fetchone()[0]
        self.assertEqual("active", tier)
        # 端到端召回链路：用 add_memory 写入的独特记忆归档→恢复→召回（每次 search 前清缓存）
        mid2 = self.add("归档恢复端到端 unique-e2e-xyz", library="project", topic_id="任务看板")
        self.store.archive_library("project", topic="任务看板", actor="main_agent", reason="x")
        server._search_cache.clear()
        self.assertTrue(all(r["id"] != mid2 for r in server.search_memories("unique-e2e-xyz", k=10, workspace_id="ws1")))
        self.store.migrate_memory(mid2, "active", 0, actor="main_agent", reason="restore")
        server._search_cache.clear()
        rr = server.search_memories("unique-e2e-xyz", k=10, workspace_id="ws1")
        self.assertTrue(any(r["id"] == mid2 for r in rr), "restored memory not recallable")
        # 既有 status 级路由保留：archive/restore 仍可用
        code, body = self.request("POST", "/v1/memories/archive", {"ids": [res["ids"][1]]})
        self.assertEqual(200, code)
        code, body = self.request("POST", "/v1/memories/restore", {"id": res["ids"][1]})
        self.assertIn(code, (200, 404))

    # ---------------- G5: document_links ----------------

    def test_document_links_for_doc(self):
        mid = self.add("契约冻结 id 对口径", library="core", topic_id="契约")
        self.store.link_document(
            mid, "/www/.../docs/deepmemory-library-model-contract.md",
            doc_kind="contract", doc_version="v0.2", relation="derived_from", workspace_id="ws1")
        links = self.store.documents_for_path("/www/.../docs/deepmemory-library-model-contract.md", workspace_id="ws1")
        self.assertEqual(1, len(links))
        self.assertEqual("derived_from", links[0]["relation"])
        mem = self.store.get_memory(mid)
        self.assertTrue(mem["doc_links"])
        self.assertEqual("contract", mem["doc_links"][0]["doc_kind"])
        # HTTP 路由
        code, body = self.request(
            "POST", "/v1/memories/doc-link",
            {"memory_id": mid, "doc_path": "/docs/plan.md", "doc_kind": "plan", "relation": "summarized_by", "workspace_id": "ws1"})
        self.assertEqual(200, code)
        code, body = self.request(
            "GET", "/v1/memories/for-doc?path=" + urllib.request.quote("/docs/plan.md", ""))
        self.assertEqual(200, code)
        self.assertEqual(1, len(body["links"]))

    # ---------------- 其他 ----------------

    def test_libraries_catalog(self):
        self.add("x1", library="core")
        stats = self.store.library_stats()
        self.assertIn("core", stats)
        self.assertGreaterEqual(stats["core"]["total"], 1)

    def test_legacy_archive_route_unchanged(self):
        mid = self.add("旧路由仍工作 legacy-route-check")
        code, body = self.request("POST", "/v1/memories/archive", {"ids": [mid]})
        self.assertEqual(200, code)
        with sqlite3.connect(server.DB_PATH) as conn:
            row = conn.execute("SELECT status, storage_tier FROM documents WHERE id=?", (mid,)).fetchone()
        self.assertEqual("archived", row[0])  # status 级，非 tier 级
        self.assertEqual("active", row[1])


if __name__ == "__main__":
    unittest.main()
