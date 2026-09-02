#!/usr/bin/env python3
"""InfoStore：域级信息注册表（按需建库）。

契约 §11：域（env/project/status/…）→ 独立库 data/info/<domain>.db（entries 表），
registry.db 维护域/键目录；无登记域（白名单外）返回 404 提示。
"""
import json
import os
import sqlite3
import time

DOMAIN_WHITELIST = ("env", "project", "status", "config", "ticket")


class InfoStore:
    def __init__(self, db_dir):
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)
        self.registry_path = os.path.join(db_dir, "registry.db")
        self._init_registry()

    def _connect(self, path):
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_registry(self):
        with self._connect(self.registry_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS domains ("
                " domain TEXT PRIMARY KEY, created_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS keys ("
                " domain TEXT NOT NULL, key TEXT NOT NULL, created_at REAL NOT NULL,"
                " PRIMARY KEY (domain, key))"
            )
            conn.commit()

    def _domain_db(self, domain):
        return os.path.join(self.db_dir, f"{domain}.db")

    def get_domain_db(self, domain):
        """按需建库：返回该域的 sqlite 连接（entries 表就绪）。"""
        if domain not in DOMAIN_WHITELIST:
            return None
        path = self._domain_db(domain)
        conn = self._connect(path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " key TEXT NOT NULL, value TEXT NOT NULL, updated_at REAL NOT NULL,"
            " PRIMARY KEY (key))"
        )
        conn.commit()
        with self._connect(self.registry_path) as rc:
            rc.execute("INSERT OR IGNORE INTO domains (domain, created_at) VALUES (?, ?)",
                       (domain, time.time()))
            rc.commit()
        return conn

    def set(self, domain, key, value):
        conn = self.get_domain_db(domain)
        if conn is None:
            return None
        try:
            conn.execute(
                "INSERT INTO entries (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )
            conn.commit()
            with self._connect(self.registry_path) as rc:
                rc.execute("INSERT OR IGNORE INTO keys (domain, key, created_at) VALUES (?, ?, ?)",
                           (domain, key, time.time()))
                rc.commit()
            return True
        finally:
            conn.close()

    def get(self, domain, key):
        path = self._domain_db(domain)
        if not os.path.isfile(path) or domain not in DOMAIN_WHITELIST:
            return None
        with self._connect(path) as conn:
            row = conn.execute("SELECT value, updated_at FROM entries WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            return {"key": key, "value": json.loads(row["value"]), "updated_at": row["updated_at"]}

    def keys(self, domain):
        path = self._domain_db(domain)
        if not os.path.isfile(path):
            return []
        with self._connect(path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT key, updated_at FROM entries ORDER BY key")]

    def domains(self):
        with self._connect(self.registry_path) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT domain, created_at FROM domains ORDER BY domain")]
