import threading
import time
import unittest

import server


class LifecycleConcurrencyTests(unittest.TestCase):
    def test_decay_interval_defaults_daily_and_is_configurable(self):
        original = server.get_setting
        try:
            server.get_setting = lambda key: 6 if key == "deepmemory.importance_decay.interval_hours" else None
            self.assertEqual(6, server.decay_interval_hours())
            server.get_setting = lambda key: None
            self.assertEqual(24, server.decay_interval_hours())
        finally:
            server.get_setting = original

    def test_bm25_remove_updates_document_frequency(self):
        bm = server.BM25()
        bm.add(1, "alpha beta")
        bm.add(2, "alpha gamma")
        self.assertEqual(2, bm.df["alpha"])
        bm.remove(1)
        self.assertEqual(1, bm.df["alpha"])
        self.assertNotIn("beta", bm.df)
        self.assertEqual([2], [doc_id for doc_id, _ in bm.search("alpha", 5)])

    def test_run_decay_serializes_concurrent_callers(self):
        original = server._run_decay
        state_lock = threading.Lock()
        state = {"active": 0, "max_active": 0, "calls": 0}

        def fake_decay(decay_rate=0.01, force=False):
            with state_lock:
                state["active"] += 1
                state["calls"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            time.sleep(0.05)
            with state_lock:
                state["active"] -= 1
            return {"decayed": 0, "archived": 0, "documents": 0}

        server._run_decay = fake_decay
        try:
            threads = [threading.Thread(target=server.run_decay) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
            self.assertEqual(4, state["calls"])
            self.assertEqual(1, state["max_active"])
        finally:
            server._run_decay = original


if __name__ == "__main__":
    unittest.main()
