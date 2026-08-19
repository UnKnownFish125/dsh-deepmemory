import json
import os
import tempfile
import unittest

import numpy as np

import server
from sensitive import redact_text


class SensitiveMemoryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="memory-sensitive-")
        server.DATA_DIR = self.root
        server.DB_PATH = os.path.join(self.root, "memory.db")
        server.INDEX_PATH = os.path.join(self.root, "memory.faiss")
        server.BACKUP_DIR = os.path.join(self.root, "backups")
        os.makedirs(server.BACKUP_DIR)
        server.embed_texts = lambda texts: [np.ones(server.DIM, dtype=np.float32) for _ in texts]
        server._index = None
        server._search_cache.clear()
        server._bm25.docs = {}
        server._bm25.df = {}
        server.init_db()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_redacts_writes_and_requires_three_use_approval(self):
        # Build a detector-shaped fixture at runtime without publishing a secret-shaped literal.
        secret = "sk" + "-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "123456"
        redacted, matches = redact_text("api_key=" + secret)
        self.assertNotIn(secret, redacted)
        self.assertTrue(matches)

        result = server.add_memory({
            "content": "service api_key=" + secret,
            "source": "source password=" + secret,
            "scope": "global",
            "user_id": "user-1",
            "session_id": "session-1",
        })
        self.assertTrue(result["sensitive"])
        conn = server.get_conn()
        row = conn.execute("SELECT content FROM documents WHERE id=?", (result["id"],)).fetchone()
        source = conn.execute("SELECT content FROM sources WHERE memory_id=?", (result["id"],)).fetchone()
        conn.close()
        self.assertNotIn(secret, row["content"])
        self.assertNotIn(secret, source["content"])

        atom_id = server.add_atom(result["id"], {
            "content": "access_token=" + secret,
            "user_id": "user-1",
            "session_id": "session-1",
        })
        card = server.put_card("workspace-1", {
            "goal": "password=" + secret,
            "user_id": "user-1",
            "session_id": "session-1",
        })
        conn = server.get_conn()
        atom = conn.execute("SELECT content FROM atoms WHERE id=?", (atom_id,)).fetchone()
        stored_card = conn.execute("SELECT goal FROM workspace_cards WHERE workspace_id='workspace-1'").fetchone()
        conn.close()
        self.assertNotIn(secret, atom["content"])
        self.assertNotIn(secret, stored_card["goal"])
        self.assertTrue(card["sensitive"])

        with self.assertRaises(PermissionError):
            server.expand_sensitive_source(result["protected_source_ids"][0], "invalid", "user-1", "session-1")

        approval = server.grant_sensitive_approval("user-1", "session-1", True)
        for _ in range(3):
            expanded = server.expand_sensitive_source(
                result["protected_source_ids"][0], approval["approval_token"], "user-1", "session-1"
            )
            self.assertIn(secret, expanded["content"])
        with self.assertRaises(PermissionError):
            server.expand_sensitive_source(
                result["protected_source_ids"][0], approval["approval_token"], "user-1", "session-1"
            )

        audit = json.dumps(server.list_sensitive_audit(), ensure_ascii=False)
        self.assertNotIn(secret, audit)

    def test_expiry_and_session_end_revoke_approval(self):
        secret = "password=temporary-secret-123"
        result = server.add_memory({"content": "use " + secret, "scope": "global"})
        approval = server.grant_sensitive_approval("user-1", "session-1", True)
        conn = server.get_conn()
        conn.execute("UPDATE sensitive_approvals SET expires_at=0 WHERE token_hash=?", (server._approval_hash(approval["approval_token"]),))
        conn.commit()
        conn.close()
        with self.assertRaises(PermissionError):
            server.expand_sensitive_source(result["protected_source_ids"][0], approval["approval_token"], "user-1", "session-1")

        fresh = server.grant_sensitive_approval("user-1", "session-2", True)
        server.end_sensitive_session("session-2")
        with self.assertRaises(PermissionError):
            server.expand_sensitive_source(result["protected_source_ids"][0], fresh["approval_token"], "user-1", "session-2")

    def test_pii_and_natural_language_passwords_are_redacted(self):
        # 银行卡号（Luhn 通过）、身份证号（校验位通过）、手机号、中文自然语言密码
        cases = [
            ("我的招商银行卡号是6225888812345670，记得转钱", "bank_card"),
            ("我身份证号110101199003078881，你记一下", "id_card"),
            ("手机号13800138000，随时联系", "phone"),
            ("我的网银密码是Abc123456", "natural_password"),
            ("网银密码Abc123456", "natural_password"),
        ]
        for text, expected in cases:
            redacted, matches = server.redact_text(text)
            self.assertTrue(matches, text)
            self.assertEqual(expected, matches[0].kind, text)
            self.assertIn("[REDACTED:%s]" % expected, redacted, text)

    def test_pii_checksum_guards_and_prose_do_not_redact(self):
        # 校验位不通过的长数字串不脱敏；普通句子不脱敏
        for text in [
            "这个项目有12345678901234个订单",
            "我买了6225888812345678（手滑的错号）",
            "密码是正确的话就登录成功",
            "PIN 码要记住",
            "今天的天气不错，出去走走",
        ]:
            redacted, matches = server.redact_text(text)
            self.assertEqual([], matches, text)
            self.assertEqual(text, redacted, text)

    def test_pii_flow_through_write_expand_audit(self):
        content = "银行卡6225888812345670，密码Abc123456，电话13800138000"
        result = server.add_memory({"content": content, "source": content, "scope": "global", "session_id": "sess-pii", "user_id": "user-pii"})
        self.assertTrue(result["sensitive"], result)
        for marker in ("6225888812345670", "Abc123456", "13800138000"):
            self.assertNotIn(marker, json.dumps(result, ensure_ascii=False))
        source_id = result["protected_source_ids"][0]
        sources = server.get_sources(result["id"])
        self.assertTrue(sources, sources)
        source = sources[0]
        self.assertTrue(source["needs_auth"], source)
        self.assertNotIn("6225888812345670", source["content"])
        with self.assertRaises(PermissionError):
            server.expand_sensitive_source(source_id, None, "user-pii", "sess-pii")
        approval = server.grant_sensitive_approval("user-pii", "sess-pii", True)
        expanded = server.expand_sensitive_source(source_id, approval["approval_token"], "user-pii", "sess-pii")
        for marker in ("6225888812345670", "Abc123456", "13800138000"):
            self.assertIn(marker, expanded["content"])
        audit = json.dumps(server.list_sensitive_audit(), ensure_ascii=False)
        self.assertNotIn("6225888812345670", audit)



if __name__ == "__main__":
    unittest.main()
