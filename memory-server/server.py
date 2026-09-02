#!/opt/AstrBot/venv/bin/python3
# -*- coding: utf-8 -*-
"""
DSH memory-server — semantic long-term memory backend for the DeepSeek Harness.

Aligned with AstrBot living memory's storage/retrieval strategy:
  - documents table (summary/key_facts/persona_summary/canonical_summary)
  - atoms table (fine-grained facts with TTL/decay)
  - graph nodes/edges (entity-relation layer)
  - FAISS IndexFlatL2 + IndexIDMap for vector search
  - BM25 (jieba tokenized) for keyword search
  - RRF fusion + alpha*relevance + beta*importance + gamma*recency weighting
  - local embedding: fastembed BAAI/bge-small-zh-v1.5 (512-dim)

HTTP API (JSON):
  GET  /v1/health
  GET  /v1/stats
  POST /v1/embeddings          {input: [text...]}
  POST /v1/memories/add        {content, type?, domain?, scope?, workspace_id?, session_id?, persona_id?, importance?, keywords?, key_facts?, persona_summary?, canonical_summary?}
  POST /v1/memories/search     {query, k?, session_id?, workspace_id?, domain?, type?, persona_id?}
  GET  /v1/memories/list       ?scope=&workspace_id=&domain=&status=&limit=
  GET  /v1/memories/<id>
  DELETE /v1/memories/<id>
  POST /v1/atoms/add           {memory_id, atom_type, content, importance?, confidence?, ttl_days?, decay_type?}
  GET  /v1/atoms/list          ?memory_id=
"""

import json
import math
import os
import shutil

# 优先使用国内镜像下载 HuggingFace 模型，避免直连超时
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import sqlite3
import threading
import time
import uuid
import ipaddress
import socket
from datetime import datetime
import hashlib
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo
import urllib.error
import urllib.request

from sensitive import redact_text, sensitivity_types

import faiss
import numpy as np

from v2_domain import (
    ConflictError,
    DomainError,
    InvalidTransition,
    NotFoundError,
    PermissionDenied,
    V2Store,
    V2_SCHEMA_VERSION,
    install_v2_schema,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "models")
DB_PATH = os.path.join(DATA_DIR, "memory.db")
INDEX_PATH = os.path.join(DATA_DIR, "memory.faiss")
DIM_PATH = os.path.join(DATA_DIR, "dim.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
PORT = int(os.environ.get("MEMORY_SERVER_PORT", "6230"))
MAX_BODY_BYTES = int(os.environ.get("MEMORY_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("MEMORY_REQUEST_TIMEOUT_SECONDS", "30"))
SECRET_CONFIG_KEYS = frozenset({"embedding.api_key"})
API_TOKEN_FILE = os.environ.get("MEMORY_API_TOKEN_FILE", os.path.join(DATA_DIR, "api-token"))
DIM = 512

# weighting: alpha*relevance + beta*importance + gamma*recency (AstrBot formula)
ALPHA, BETA, GAMMA = 0.5, 0.25, 0.25
DECAY_RATE = 0.01
RRF_K = 60

STOPWORDS = set(
    "的 了 和 是 就 都 而 及 与 着 或 一个 没有 我们 你们 他们 这个 那个 什么 怎么 "
    "在 有 被 把 对 从 到 上 下 中 我 你 他 她 它 也 很 不 吗 呢 吧 啊 呀".split()
)

os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------- embedding

_embed_lock = threading.Lock()
_embed_model = None


def get_embed_config():
    """读取 embedding.* 配置，未设置时回落到 config_schema 默认值。"""
    values = get_config_values(include_secrets=True)
    items = get_config_schema().get("embedding", {}).get("items", {})
    cfg = {k: meta.get("default") for k, meta in items.items()}
    for k in cfg:
        v = values.get("embedding." + k)
        if v is not None:
            cfg[k] = v
    return cfg


def get_embed_dim():
    """当前嵌入向量维度：首次由模型输出确定并持久化，默认 512 兼容旧库。"""
    try:
        with open(DIM_PATH, encoding="utf-8") as fh:
            return int(json.load(fh)["dim"])
    except Exception:
        return DIM


def set_embed_dim(dim):
    dim = int(dim)
    try:
        with open(DIM_PATH, encoding="utf-8") as fh:
            if json.load(fh).get("dim") == dim:
                return
    except Exception:
        pass
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DIM_PATH, "w", encoding="utf-8") as fh:
        json.dump({"dim": dim}, fh)


def get_model():
    """本地 fastembed 模型（provider=local），懒加载单例。"""
    global _embed_model
    with _embed_lock:
        if _embed_model is None:
            cfg = get_embed_config()
            from fastembed import TextEmbedding

            _embed_model = TextEmbedding(
                model_name=cfg.get("local_model") or "BAAI/bge-small-zh-v1.5",
                cache_dir=MODEL_DIR,
            )
            list(_embed_model.embed(["warmup"]))
        return _embed_model


def _validate_embedding_base_url(base):
    """Require HTTPS and a public destination to prevent SSRF and key exfiltration."""
    parsed = urlparse(base)
    allow_private = os.environ.get("MEMORY_ALLOW_PRIVATE_EMBEDDING", "") == "1"
    if parsed.scheme not in ({"https", "http"} if allow_private else {"https"}) or not parsed.hostname:
        raise ValueError("embedding api_base_url must be an HTTPS public endpoint")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("embedding api_base_url contains forbidden URL components")
    if allow_private:
        return base.rstrip("/")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("embedding api_base_url hostname cannot be resolved") from exc
    if not addresses:
        raise ValueError("embedding api_base_url hostname cannot be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global:
            raise ValueError("embedding api_base_url must resolve to a public address")
    return base.rstrip("/")


def _validate_embedding_destination(base):
    """Re-check DNS immediately before connecting; redirects are disabled below."""
    _validate_embedding_base_url(base)
    return base


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "embedding redirects are disabled", headers, fp)


def _embed_api(texts):
    """OpenAI 兼容 /v1/embeddings（provider=api）。"""
    cfg = get_embed_config()
    base = (cfg.get("api_base_url") or "").rstrip("/")
    key = cfg.get("api_key") or os.environ.get("EMBED_API_KEY", "")
    model = cfg.get("api_model") or "text-embedding-3-small"
    if not base or not key:
        raise RuntimeError(
            "embedding provider=api 需要 api_base_url 与 api_key（或环境变量 EMBED_API_KEY）"
        )
    base = _validate_embedding_destination(base)
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        base + "/embeddings",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    out = []
    for item in sorted(payload["data"], key=lambda x: x.get("index", 0)):
        out.append(np.asarray(item["embedding"], dtype=np.float32))
    if not out:
        raise RuntimeError("embedding API 返回空结果")
    return out


def embed_texts(texts):
    texts = [str(t or "") for t in texts]
    if not texts:
        return []
    provider = (get_embed_config().get("provider") or "local").lower()
    if provider == "api":
        vecs = _embed_api(texts)
    else:
        model = get_model()
        vecs = [np.asarray(v, dtype=np.float32) for v in model.embed(texts)]
    if vecs:
        set_embed_dim(vecs[0].shape[0])
    return vecs


# ---------------------------------------------------------------- sqlite

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
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

CREATE TABLE IF NOT EXISTS document_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL REFERENCES documents(id),
  doc_path TEXT NOT NULL,
  doc_kind TEXT NOT NULL DEFAULT 'note'
    CHECK(doc_kind IN ('contract','plan','proposal','code','note')),
  doc_version TEXT DEFAULT '',
  relation TEXT NOT NULL
    CHECK(relation IN ('derived_from','summarized_by','superseded_by')),
  workspace_id TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_document_links_doc ON document_links(doc_path);
CREATE INDEX IF NOT EXISTS idx_document_links_mem ON document_links(memory_id);
-- 校验：memory_id 必须存在
CREATE TRIGGER IF NOT EXISTS trg_document_links_mem_check
BEFORE INSERT ON document_links
WHEN NEW.memory_id NOT IN (SELECT id FROM documents)
BEGIN SELECT RAISE(ABORT, 'document_links memory_id not found'); END;
CREATE TABLE IF NOT EXISTS atoms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  atom_type TEXT DEFAULT 'unknown',
  content TEXT NOT NULL,
  entities TEXT DEFAULT '[]',
  importance REAL DEFAULT 0.5,
  confidence REAL DEFAULT 0.7,
  ttl_days REAL DEFAULT 30,
  decay_type TEXT DEFAULT 'exponential',
  status TEXT DEFAULT 'active',
  created_at REAL,
  last_accessed_at REAL,
  last_reinforced_at REAL,
  reinforcement_count INTEGER DEFAULT 0,
  expires_at REAL DEFAULT 0,
  has_sensitive INTEGER DEFAULT 0,
  sensitive_types TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS graph_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT DEFAULT 'entity',
  created_at REAL
);
CREATE TABLE IF NOT EXISTS graph_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  target_id INTEGER NOT NULL,
  relation TEXT DEFAULT '',
  memory_id INTEGER,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS workspace_cards (
  workspace_id TEXT PRIMARY KEY,
  goal TEXT DEFAULT '',
  current_plan TEXT DEFAULT '',
  key_decisions TEXT DEFAULT '[]',
  in_progress TEXT DEFAULT '[]',
  next_steps TEXT DEFAULT '[]',
  version INTEGER DEFAULT 0,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at REAL,
  protected_source_id INTEGER
);
CREATE TABLE IF NOT EXISTS protected_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER,
  resource_type TEXT NOT NULL DEFAULT 'memory',
  resource_id TEXT,
  field_name TEXT NOT NULL,
  content TEXT NOT NULL,
  redacted_content TEXT NOT NULL,
  sensitivity_types TEXT NOT NULL DEFAULT '[]',
  owner_user_id TEXT DEFAULT '',
  owner_session_id TEXT DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sensitive_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event TEXT NOT NULL,
  protected_source_id INTEGER,
  memory_id INTEGER,
  resource_type TEXT DEFAULT '',
  resource_id TEXT DEFAULT '',
  user_id TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  sensitivity_types TEXT DEFAULT '[]',
  details TEXT DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sensitive_approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  granted_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  max_uses INTEGER NOT NULL,
  use_count INTEGER NOT NULL DEFAULT 0,
  revoked_at REAL
);
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  applied_at REAL
);
"""


def run_migrations():
    """Apply backward-compatible, repeatable schema migrations."""
    migrations = {
        2: [
            "CREATE INDEX IF NOT EXISTS idx_graph_edges_memory ON graph_edges(memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_atoms_memory ON atoms(memory_id)",
        ],
        3: [
            "ALTER TABLE documents ADD COLUMN has_sensitive INTEGER DEFAULT 0",
            "ALTER TABLE documents ADD COLUMN sensitive_types TEXT DEFAULT '[]'",
            "ALTER TABLE atoms ADD COLUMN has_sensitive INTEGER DEFAULT 0",
            "ALTER TABLE atoms ADD COLUMN sensitive_types TEXT DEFAULT '[]'",
            "ALTER TABLE sources ADD COLUMN protected_source_id INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_protected_sources_resource ON protected_sources(resource_type, resource_id)",
            "CREATE INDEX IF NOT EXISTS idx_sensitive_audit_source ON sensitive_audit(protected_source_id)",
        ],
        4: [
            "ALTER TABLE documents ADD COLUMN topic_id TEXT DEFAULT ''",
            "ALTER TABLE documents ADD COLUMN event_time REAL",
            "CREATE INDEX IF NOT EXISTS idx_documents_topic ON documents(topic_id)",
        ],
        5: [
            "ALTER TABLE documents ADD COLUMN library TEXT NOT NULL DEFAULT 'runtime'",
            "CREATE INDEX IF NOT EXISTS idx_documents_library ON documents(library)",
            "CREATE TABLE IF NOT EXISTS document_links (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL REFERENCES documents(id), doc_path TEXT NOT NULL, doc_kind TEXT NOT NULL DEFAULT 'note' CHECK(doc_kind IN ('contract','plan','proposal','code','note')), doc_version TEXT DEFAULT '', relation TEXT NOT NULL CHECK(relation IN ('derived_from','summarized_by','superseded_by')), workspace_id TEXT DEFAULT '', created_at REAL NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_document_links_doc ON document_links(doc_path)",
            "CREATE INDEX IF NOT EXISTS idx_document_links_mem ON document_links(memory_id)",
        ],
        6: [
            "ALTER TABLE documents ADD COLUMN rule_crystallized INTEGER NOT NULL DEFAULT 0",
        ],
        7: [
            "CREATE TABLE IF NOT EXISTS topic_summaries ("
            " topic_id TEXT NOT NULL, seq INTEGER NOT NULL, summary TEXT NOT NULL,"
            " start_time REAL NOT NULL, end_time REAL NOT NULL, prev_seq INTEGER,"
            " created_at REAL NOT NULL, PRIMARY KEY (topic_id, seq))",
            "CREATE INDEX IF NOT EXISTS idx_topic_summaries ON topic_summaries(topic_id, seq)",
        ],
    }
    conn = get_conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at REAL)"
    )
    cur = conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
    current = cur["v"] if cur and cur["v"] is not None else 0
    if current < 1:
        # v1 基线：documents/atoms/graph/settings/workspace_cards/sources 均已建
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (1, ?)",
            (time.time(),),
        )
        conn.commit()
        print("migration: schema v1 基线已应用", flush=True)
        current = 1
    for version in sorted(migrations):
        if current < version:
            for stmt in migrations[version]:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as exc:
                    # v3 columns are present in fresh databases via SCHEMA;
                    # tolerate the duplicate ALTER when upgrading those DBs.
                    if not (stmt.startswith("ALTER TABLE") and "duplicate column" in str(exc).lower()):
                        raise
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?,?)",
                (version, time.time()),
            )
            conn.commit()
            print(f"migration: schema v{version} 已应用", flush=True)
            current = version
    # The v2 domain installer is intentionally run on every startup. Its DDL is
    # idempotent, so it can also repair a database copied during a partial
    # migration without renaming or dropping any legacy object.
    install_v2_schema(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?,?)",
        (V2_SCHEMA_VERSION, time.time()),
    )
    conn.commit()
    if current < V2_SCHEMA_VERSION:
        print(f"migration: schema v{V2_SCHEMA_VERSION} 已应用", flush=True)
    conn.close()


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    run_migrations()
    # v2 存储域：启动时确保 install_v2_schema 的列/索引迁移（幂等；懒 V2Store.migrate 前）
    try:
        _conn = get_conn()
        try:
            install_v2_schema(_conn)
        finally:
            _conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------- faiss

_index_lock = threading.RLock()  # RLock：get_index 持锁触发迁移重建时可重入
_index = None
# 检索缓存：{(query,k,session,workspace,domain,type,persona): (ts, results)}
_search_cache = {}


def get_index():
    global _index
    with _index_lock:
        if _index is None:
            has_dim_meta = os.path.exists(DIM_PATH)
            dim = get_embed_dim() if has_dim_meta else None
            if os.path.exists(INDEX_PATH):
                if has_dim_meta:
                    _index = faiss.read_index(INDEX_PATH)
                    if _index.d != dim:
                        # 嵌入维度切换（如 local→api 或模型更换）：按新维度影子重建
                        _index = None
                        _rebuild_indexes_internal()
                        _index = faiss.read_index(INDEX_PATH)
                else:
                    # 配置变更后维度未确定：强制按当前配置重建
                    _rebuild_indexes_internal()
                    _index = faiss.read_index(INDEX_PATH)
            else:
                _index = faiss.IndexIDMap(faiss.IndexFlatL2(dim if dim is not None else DIM))
        return _index


def save_index():
    with _index_lock:
        if _index is not None:
            faiss.write_index(_index, INDEX_PATH)


# ---------------------------------------------------------------- bm25

def tokenize(text):
    import jieba

    words = []
    for w in jieba.lcut(text or ""):
        w = w.strip().lower()
        if len(w) <= 1 or w in STOPWORDS:
            continue
        words.append(w)
    return words


class BM25:
    def __init__(self):
        self._lock = threading.RLock()
        self.docs = {}
        self.df = {}
        self.avgdl = 1.0

    def add(self, doc_id, text):
        tokens = tokenize(text)
        if not tokens:
            return
        with self._lock:
            self.docs[doc_id] = tokens
            for token in set(tokens):
                self.df[token] = self.df.get(token, 0) + 1
            total = sum(len(v) for v in self.docs.values())
            self.avgdl = total / max(1, len(self.docs))

    def remove(self, doc_id):
        with self._lock:
            removed = self.docs.pop(doc_id, None)
            if removed:
                for token in set(removed):
                    count = self.df.get(token, 0) - 1
                    if count > 0:
                        self.df[token] = count
                    else:
                        self.df.pop(token, None)
            total = sum(len(v) for v in self.docs.values())
            self.avgdl = total / max(1, len(self.docs))

    def replace(self, rows):
        docs = {}
        df = {}
        for doc_id, text in rows:
            tokens = tokenize(text)
            if not tokens:
                continue
            docs[doc_id] = tokens
            for token in set(tokens):
                df[token] = df.get(token, 0) + 1
        total = sum(len(v) for v in docs.values())
        with self._lock:
            self.docs = docs
            self.df = df
            self.avgdl = total / max(1, len(docs))

    def search(self, query, k):
        tokens = tokenize(query)
        if not tokens:
            return []
        with self._lock:
            docs = list(self.docs.items())
            df = dict(self.df)
            avgdl = self.avgdl
        n = max(1, len(docs))
        scores = {}
        for t in tokens:
            if t not in df:
                continue
            idf = math.log(1.0 + (n - df[t] + 0.5) / (df[t] + 0.5))
            for doc_id, doc_tokens in docs:
                tf = doc_tokens.count(t)
                if tf == 0:
                    continue
                dl = len(doc_tokens)
                denom = tf + 1.5 * (1 - 0.75 + 0.75 * dl / max(1.0, avgdl))
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * 1.5) / denom
        return sorted(scores.items(), key=lambda kv: -kv[1])[:k]


_last_injection = None
_injection_lock = threading.Lock()

def note_injection(query, results):
    """记录最近一次注入（WebUI 显示用；内存级，避免每回合写盘）。"""
    global _last_injection
    with _injection_lock:
        _last_injection = {
            "ts": time.time(),
            "query": str(query or "")[:120],
            "ids": [int(r.get("id") or 0) for r in results[:20] if r.get("id")],
            "top": [str(r.get("content") or "")[:100] for r in results[:5]],
            "count": len(results or []),
        }


_info_store = None

def get_info_store():
    global _info_store
    if _info_store is None:
        from info_store import InfoStore
        _info_store = InfoStore(os.path.join(DATA_DIR, "info"))
    return _info_store


def _credentials_map():
    """读取 DSH 凭据文件（refs.*），key 引用：credentials['UUAPI_GPT_API_KEY']。"""
    dsh_home = os.environ.get("DSH_HOME", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "dsh"))
    for cand in (os.path.join(dsh_home, ".credentials.yaml"), "/www/dsh/home/.credentials.yaml"):
        if os.path.isfile(cand):
            try:
                import yaml as _yaml
                doc = _yaml.safe_load(open(cand, encoding="utf-8"))
                return (doc.get("refs") or doc) or {}
            except Exception:
                continue
    return {}


def llm_chat(prompt, model=None, system=None, provider=None, api_key=None):
    """夜间批处理 LLM 提炼（低成本默认：uuapi/deepseek-v4-flash-0731，谷时批处理）。

    provider=http 兼容 endpoints；key 来自 .credentials.yaml refs.UUAPI_GPT_API_KEY。
    """
    model = model or "deepseek-v4-flash-0731"
    credentials = _credentials_map()
    key = api_key or credentials.get("UUAPI_API_KEY") or credentials.get("UUAPI_GPT_API_KEY") or ""
    if not key:
        return {"error": "no api key (refs.UUAPI_API_KEY)"}
    base = (provider or "https://uuapi.io/v1").rstrip("/")
    url = base + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {"model": model, "messages": messages, "max_tokens": 512, "temperature": 0.3}
    # 模型链 fallback：0731 波动（502）时回落 deepseek-v4-flash（同价/稍差，保证批处理可用）
    model_chain = []
    if model:
        model_chain.append(model)
    for m in ("deepseek-v4-flash", "deepseek-v4-flash-0731"):
        if m and m not in model_chain:
            model_chain.append(m)
    last_err = None
    for m in model_chain:
        body["model"] = m
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={
                "Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {"content": content, "usage": data.get("usage"), "model": m}
        except Exception as e:
            last_err = redact_text(str(e))[0]
    return {"error": last_err or "all models failed"}


_bm25 = BM25()


def get_bm25():
    return _bm25


def rebuild_bm25():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, key_facts, keywords FROM documents WHERE status='active' AND storage_tier='active'"
    ).fetchall()
    conn.close()
    bm = get_bm25()
    bm.replace([
        (r["id"], " ".join(x for x in (r["content"], r["key_facts"], r["keywords"]) if x))
        for r in rows
    ])


# ---------------------------------------------------------------- retrieval

def normalize(vec):
    n = float(np.linalg.norm(vec))
    return vec / n if n > 0 else vec


def vector_search(query_vec, k):
    idx = get_index()
    if idx.ntotal == 0:
        return []
    q = np.asarray([normalize(query_vec)], dtype=np.float32)
    scores, ids = idx.search(q, min(k, idx.ntotal))
    out = []
    seen = set()
    for i in range(len(ids[0])):
        doc_id = int(ids[0][i])
        if doc_id < 0 or doc_id in seen:
            continue
        seen.add(doc_id)
        dist = float(scores[0][i])
        # unit vectors: L2^2 = 2(1-cos)
        cos_sim = max(0.0, 1.0 - (dist * dist) / 2.0)
        out.append((doc_id, cos_sim))
    return out


def graph_search(query, k, include_archived=False):
    """图检索路：query 命中实体节点 → 沿边找关联记忆 + 实体名命中记忆文本。"""
    tokens = tokenize(query)
    if not tokens:
        return []
    conn = get_conn()
    matched = []
    for t in tokens[:12]:
        if len(t) < 2:
            continue
        rows = conn.execute(
            "SELECT name FROM graph_nodes WHERE name LIKE ? LIMIT 20", ("%" + t + "%",)
        ).fetchall()
        matched.extend(r["name"] for r in rows)
    matched = list(dict.fromkeys(matched))
    if not matched:
        conn.close()
        return []
    mem_scores = {}
    qmarks = ",".join("?" * len(matched))
    nodes = conn.execute(
        f"SELECT id FROM graph_nodes WHERE name IN ({qmarks})", matched
    ).fetchall()
    node_ids = [n["id"] for n in nodes]
    if node_ids:
        q2 = ",".join("?" * len(node_ids))
        edges = conn.execute(
            f"SELECT memory_id FROM graph_edges WHERE source_id IN ({q2}) OR target_id IN ({q2})",
            node_ids * 2,
        ).fetchall()
        for e in edges:
            if e["memory_id"]:
                mem_scores[e["memory_id"]] = mem_scores.get(e["memory_id"], 0.0) + 0.4
    for name in matched[:12]:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status='active'"
            + ("" if include_archived else " AND storage_tier='active'")
            + " AND (content LIKE ? OR key_facts LIKE ?) LIMIT 8",
            ("%" + name + "%", "%" + name + "%"),
        ).fetchall()
        for r in rows:
            mem_scores[r["id"]] = mem_scores.get(r["id"], 0.0) + 0.3
    conn.close()
    ranked = sorted(mem_scores.items(), key=lambda kv: -kv[1])
    return [(i, s) for i, s in ranked[:k]]


def cfg_float(key, default):
    try:
        v = get_setting("deepmemory." + key)
        return float(v) if v is not None else default
    except Exception:
        return default


def cfg_bool(key, default):
    v = get_setting("deepmemory." + key)
    if v is None:
        return default
    if isinstance(v, str):
        normalized = v.strip().lower()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off", ""):
            return False
    return bool(v)


def rrf_fuse(ranked_lists):
    rrf_k = int(cfg_float("fusion_strategy.rrf_k", RRF_K))
    fused = {}
    for ranked in ranked_lists:
        for rank, (doc_id, _score) in enumerate(ranked):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)
    return sorted(fused.items(), key=lambda kv: -kv[1])


def apply_weighting(fused, now=None, include_archived=False):
    if not fused:
        return []
    alpha = cfg_float("fusion_strategy.alpha", ALPHA)
    beta = cfg_float("fusion_strategy.beta", BETA)
    gamma = cfg_float("fusion_strategy.gamma", GAMMA)
    decay_rate = cfg_float("importance_decay.decay_rate", DECAY_RATE)
    now = now or time.time()
    conn = get_conn()
    ids = [i for i, _ in fused]
    if not ids:
        return []
    qmarks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM documents WHERE id IN ({qmarks}) AND status='active'"
        + ("" if include_archived else " AND storage_tier='active'"), ids
    ).fetchall()
    conn.close()
    meta = {r["id"]: r for r in rows}
    max_score = max(s for _, s in fused) or 1.0
    results = []
    for doc_id, rrf in fused:
        r = meta.get(doc_id)
        if r is None:
            continue
        importance = max(0.0, min(1.0, float(r["importance"] or 0.5)))
        ref = max(float(r["created_at"] or now), float(r["last_access_at"] or 0))
        days_old = max(0.0, (now - ref) / 86400.0)
        recency = math.exp(-DECAY_RATE * days_old)
        norm = rrf / max_score
        final = ALPHA * norm + BETA * importance + GAMMA * recency
        # 偏好/高重要性记忆保底：relevance 弱时不被语义不匹配淹没（用户约定、校验规则类记忆保持曝光）
        mtype = str(r["type"] or "")
        if mtype == "preference" and importance >= 0.7:
            final += 0.35 * importance
        elif importance >= 0.7:
            final += 0.18 * importance
        item = dict(r)
        item["rrf_score"] = round(rrf, 4)
        item["final_score"] = round(final, 4)
        item["recency_weight"] = round(recency, 4)
        item["days_old"] = round(days_old, 2)
        # ① 绝对时间：入库/最后访问的本地时间字符串
        import datetime
        item["created_local"] = datetime.datetime.fromtimestamp(float(r["created_at"] or now)).strftime("%Y-%m-%d %H:%M")
        item["accessed_local"] = datetime.datetime.fromtimestamp(float(r["last_access_at"] or r["created_at"] or now)).strftime("%Y-%m-%d %H:%M")
        results.append(item)
    results.sort(key=lambda x: -x["final_score"])
    return results


def scope_allows(r, session_id, workspace_id):
    s = r["scope"]
    if s == "global":
        return True
    if s == "workspace":
        return bool(workspace_id) and r["workspace_id"] == workspace_id
    if s == "session":
        return bool(session_id) and r["session_id"] == session_id
    return True


def search_memories(
    query,
    k=5,
    session_id=None,
    workspace_id=None,
    domain=None,
    type_=None,
    persona_id=None,
    library=None,
    include_archived=False,
):
    if not query or not query.strip():
        return []
    k = max(1, min(int(k), 50))
    # 检索缓存：相同查询参数组合在 TTL 内直接复用（living memory 检索缓存）
    cache_enabled = cfg_bool("search_cache.enabled", True)
    cache_key = (query.strip()[:200], k, session_id or "", workspace_id or "", domain or "", type_ or "", persona_id or "", library or "", bool(include_archived))
    if cache_enabled:
        hit = _search_cache.get(cache_key)
        if hit and time.time() - hit[0] < cfg_float("search_cache.ttl_seconds", 45.0):
            return hit[1]
    vecs = embed_texts([query])
    vres = vector_search(vecs[0], k * 3)
    bres = get_bm25().search(query, k * 3)
    gres = graph_search(query, k * 3, include_archived=include_archived)
    fused = rrf_fuse([vres, bres, gres])
    weighted = apply_weighting(fused, include_archived=include_archived)
    out = []
    for r in weighted:
        if not scope_allows(r, session_id, workspace_id):
            continue
        if domain and r["domain"] != domain:
            continue
        if type_:
            _types = [x.strip() for x in str(type_).split(",") if x.strip()]
            if _types and str(r.get("type") or "") not in _types:
                continue
            continue
        if persona_id and r["persona_id"] and r["persona_id"] != persona_id:
            continue
        if library and r.get("library") != library:
            continue
        # G1 修复：archive tier 默认不召回（include_archived=true 时放开）
        if not include_archived and r.get("storage_tier") == "archive":
            continue
        item = dict(r)
        if item.get("has_sensitive"):
            item["sensitive_notice"] = "相关受保护来源存在；默认不展开原文"
            conn = get_conn()
            item["protected_source_count"] = conn.execute(
                "SELECT COUNT(*) c FROM protected_sources WHERE memory_id=?", (item["id"],)
            ).fetchone()["c"]
            conn.close()
        out.append(item)
        if len(out) >= k:
            break
    # update access stats
    if out:
        now = time.time()
        conn = get_conn()
        for r in out:
            conn.execute(
                "UPDATE documents SET last_access_at=?, access_count=access_count+1 WHERE id=?",
                (now, r["id"]),
            )
        # 访问强化：被召回的活跃原子刷新强化时间并延长 TTL（living memory access reinforcement）
        if cfg_bool("access_reinforcement.reinforce_atoms", True):
            extension = max(0.0, cfg_float("access_reinforcement.atom_ttl_extension_days", 1.0))
            for r in out:
                conn.execute(
                    "UPDATE atoms SET reinforcement_count=reinforcement_count+1,"
                    " last_reinforced_at=?, expires_at=CASE WHEN expires_at>0 AND expires_at<?"
                    " THEN ? ELSE expires_at END WHERE memory_id=? AND status='active'",
                    (now, now + extension * 86400.0, now + extension * 86400.0, r["id"]),
                )
        conn.commit()
        conn.close()
    if cache_enabled and out:
        if len(_search_cache) >= int(cfg_float("search_cache.max_entries", 256)):
            oldest = min(_search_cache, key=lambda ck: _search_cache[ck][0], default=None)
            if oldest is not None:
                _search_cache.pop(oldest, None)
        _search_cache[cache_key] = (time.time(), out)
    return out


def add_memory(payload):
    payload = dict(payload or {})
    clean, records = _sanitize_text_fields(
        payload,
        ("content", "key_facts", "persona_summary", "canonical_summary", "keywords"),
    )
    content = (clean.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    now = time.time()
    uuid_s = clean.get("uuid") or str(uuid.uuid4())
    importance = float(clean.get("importance", 0.5) or 0.5)
    key_facts = clean.get("key_facts") or ""
    persona_summary = clean.get("persona_summary") or ""
    canonical_summary = clean.get("canonical_summary") or ""
    clean = _mark_sensitive(clean, records)
    # ② 系统兜底：来自当前对话（dialog_scoped）的写入，若模型判 workspace 且有具体会话 → 回落 session
    if clean.get("dialog_scoped") and clean.get("session_id"):
        sc = str(clean.get("scope") or "")
        if sc == "workspace":
            clean["scope"] = "session"
    clean.pop("dialog_scoped", None)
    # 检索文本 = 摘要 + 关键事实，提升检索信息密度（对齐 living memory）
    search_text = content + (" " + key_facts if key_facts else "")
    vecs = embed_texts([search_text])
    vec = normalize(vecs[0])
    merge_threshold = cfg_float("fusion_strategy.merge_similarity_threshold", 0.85)
    # near-duplicate merge: same-meaning memory updates the existing entry
    top = vector_search(vec, 1)
    if top:
        top_id, top_sim = top[0]
        if top_sim > merge_threshold:
            conn = get_conn()
            conn.execute(
                "UPDATE documents SET content=?, key_facts=?, persona_summary=?,"
                " canonical_summary=?, importance=MAX(importance, ?),"
                " last_access_at=?, access_count=access_count+1, has_sensitive=?,"
                " sensitive_types=? WHERE id=?",
                (content, key_facts, persona_summary, canonical_summary, importance, now,
                 int(clean.get("has_sensitive") or 0), json.dumps(clean.get("sensitive_types") or [], ensure_ascii=False), top_id),
            )
            conn.commit()
            conn.close()
            for field, original, redacted, matches in records:
                _record_protected(top_id, "memory", top_id, field, original, redacted, matches, payload)
            _save_source(top_id, payload.get("source") or "", payload)
            bm = get_bm25()
            bm.remove(top_id)
            bm.add(top_id, search_text + " " + (clean.get("keywords") or ""))
            return {"id": top_id, "uuid": uuid_s, "merged": True,
                    "sensitive": bool(records), "protected_source_ids": _protected_ids(top_id)}
    conn = get_conn()
    library = str(clean.get("library") or "runtime")
    if library not in ("bias", "core", "eco", "project", "runtime"):
        conn.close()
        raise ValueError("invalid library: " + library)
    if library == "bias":
        if str(clean.get("scope") or "session") != "global":
            conn.close()
            raise ValueError("bias library requires scope=global")
        if importance < 0.8:
            conn.close()
            raise ValueError("bias library requires importance>=0.8")
    cur = conn.execute(
        "INSERT INTO documents (uuid, content, key_facts, persona_summary,"
        " canonical_summary, type, domain, scope, workspace_id, session_id,"
        " persona_id, importance, created_at, last_access_at, access_count,"
        " status, keywords, has_sensitive, sensitive_types, topic_id, event_time, library)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            uuid_s,
            content,
            key_facts,
            persona_summary,
            canonical_summary,
            clean.get("type") or "fact",
            clean.get("domain") or "work",
            clean.get("scope") or "session",
            clean.get("workspace_id") or "",
            clean.get("session_id") or "",
            clean.get("persona_id") or "",
            importance,
            now,
            now,
            0,
            "active",
            clean.get("keywords") or "",
            int(clean.get("has_sensitive") or 0),
            json.dumps(clean.get("sensitive_types") or [], ensure_ascii=False),
            clean.get("topic_id") or "",
            clean.get("event_time") if clean.get("event_time") is not None else now,
            library,
        ),
    )
    doc_id = cur.lastrowid
    conn.commit()
    conn.close()
    protected_ids = []
    for field, original, redacted, matches in records:
        protected_ids.append(_record_protected(doc_id, "memory", doc_id, field, original, redacted, matches, payload))
    protected_ids.extend(_save_source(doc_id, payload.get("source") or "", payload) or [])
    idx = get_index()
    with _index_lock:
        idx.add_with_ids(vec.reshape(1, -1), np.asarray([doc_id], dtype=np.int64))
    save_index()
    get_bm25().add(doc_id, search_text + " " + (clean.get("keywords") or ""))
    return {"id": doc_id, "uuid": uuid_s, "merged": False,
            "sensitive": bool(records), "protected_source_ids": protected_ids}


def _protected_ids(memory_id):
    conn = get_conn()
    rows = conn.execute("SELECT id FROM protected_sources WHERE memory_id=? ORDER BY id", (int(memory_id),)).fetchall()
    conn.close()
    return [row["id"] for row in rows]


def _save_source(memory_id, source_text, payload=None):
    """原文保留：高价值记忆的来源消息全文存入 sources 表（审计/回溯用）。"""
    source_text = (source_text or "").strip()
    if not source_text:
        return []
    redacted, matches = redact_text(source_text)
    protected_id = None
    if matches:
        protected_id = _record_protected(memory_id, "memory", memory_id, "source", source_text, redacted, matches, payload)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sources (memory_id, content, created_at, protected_source_id) VALUES (?,?,?,?)",
        (int(memory_id), redacted[:8000], time.time(), protected_id),
    )
    conn.commit()
    conn.close()
    return [protected_id] if protected_id else []


def add_atom(memory_id, payload):
    payload = dict(payload or {})
    clean, records = _sanitize_text_fields(payload, ("content", "entities"), resource_type="atom", resource_id=memory_id, memory_id=memory_id)
    now = time.time()
    ttl = float(clean.get("ttl_days", 30) or 30)
    content = (clean.get("content") or "").strip()
    # 原子级查重（living memory 对齐）：与同记忆的活跃原子 Jaccard 相似，
    # 超过阈值视为重复 → 强化已有原子（计数+刷新+延长 TTL），不再新增。
    if content:
        threshold = cfg_float("fusion_strategy.atom_dedup_threshold", 0.6)
        conn = get_conn()
        existing = conn.execute(
            "SELECT id, content FROM atoms WHERE memory_id=? AND status='active'",
            (int(memory_id),),
        ).fetchall()
        new_tokens = set(tokenize(content))
        if new_tokens:
            for row in existing:
                old_tokens = set(tokenize(row["content"]))
                union = new_tokens | old_tokens
                inter = new_tokens & old_tokens
                jaccard = len(inter) / len(union) if union else 0.0
                if jaccard >= threshold:
                    conn.execute(
                        "UPDATE atoms SET reinforcement_count=reinforcement_count+1,"
                        " last_accessed_at=?, last_reinforced_at=?, expires_at=CASE"
                        " WHEN expires_at>0 AND expires_at<? THEN ? ELSE expires_at END WHERE id=?",
                        (now, now, now + ttl * 86400.0, now + ttl * 86400.0, row["id"]),
                    )
                    conn.commit()
                    conn.close()
                    return row["id"]
        conn.close()
    clean = _mark_sensitive(clean, records)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO atoms (memory_id, atom_type, content, entities, importance,"
        " confidence, ttl_days, decay_type, status, created_at, last_accessed_at,"
        " reinforcement_count, expires_at, has_sensitive, sensitive_types) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(memory_id),
            clean.get("atom_type") or "unknown",
            content,
            json.dumps(clean.get("entities") or [], ensure_ascii=False),
            float(clean.get("importance", 0.5) or 0.5),
            float(clean.get("confidence", 0.7) or 0.7),
            ttl,
            clean.get("decay_type") or "exponential",
            "active",
            now,
            now,
            0,
            now + ttl * 86400,
            int(clean.get("has_sensitive") or 0),
            json.dumps(clean.get("sensitive_types") or [], ensure_ascii=False),
        ),
    )
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    for field, original, redacted, matches in records:
        _record_protected(memory_id, "atom", aid, field, original, redacted, matches, payload)
    return aid


def add_entity(memory_id, payload):
    payload = dict(payload or {}) if isinstance(payload, dict) else {}
    original_name = (payload.get("name") or "").strip()
    name, matches = redact_text(original_name)
    name = name.strip()
    if not name:
        return None
    now = time.time()
    conn = get_conn()
    node = conn.execute("SELECT id FROM graph_nodes WHERE name=?", (name,)).fetchone()
    if node is None:
        cur = conn.execute(
            "INSERT INTO graph_nodes (name, kind, created_at) VALUES (?,?,?)",
            (name, payload.get("kind") or "entity", now),
        )
        nid = cur.lastrowid
    else:
        nid = node["id"]
    conn.commit()
    conn.close()
    if matches:
        _record_protected(memory_id, "entity", nid, "name", original_name, name, matches, payload)
    return nid


def add_relation(memory_id, payload):
    """新增实体关系边：source/relation/target；节点不存在时自动创建。"""
    src = (payload.get("source") or "").strip() if isinstance(payload, dict) else ""
    dst = (payload.get("target") or "").strip() if isinstance(payload, dict) else ""
    rel_original = (payload.get("relation") or "").strip() if isinstance(payload, dict) else ""
    rel, rel_matches = redact_text(rel_original)
    if not src or not dst:
        return None
    sid = add_entity(memory_id, {"name": src})
    tid = add_entity(memory_id, {"name": dst})
    if sid is None or tid is None:
        return None
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM graph_edges WHERE source_id=? AND target_id=? AND relation=?",
        (sid, tid, rel),
    ).fetchone()
    if existing is not None:
        conn.close()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO graph_edges (source_id, target_id, relation, memory_id, created_at) VALUES (?,?,?,?,?)",
        (sid, tid, rel, int(memory_id), time.time()),
    )
    conn.commit()
    conn.close()
    if rel_matches:
        _record_protected(memory_id, "relation", cur.lastrowid, "relation", rel_original, rel, rel_matches, payload)
    return cur.lastrowid


def get_sources(memory_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, created_at, protected_source_id FROM sources WHERE memory_id=? ORDER BY id DESC",
        (int(memory_id),),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["protected"] = bool(item.get("protected_source_id"))
        if item["protected"]:
            protected = _load_protected(item["protected_source_id"])
            item["content"] = protected["redacted_content"] if protected else "[sensitive content redacted]"
            item["needs_auth"] = True
        out.append(item)
    return out


def delete_memory(doc_id):
    conn = get_conn()
    row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        conn.close()
        return False
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.execute("DELETE FROM atoms WHERE memory_id=?", (doc_id,))
    conn.execute("DELETE FROM graph_edges WHERE memory_id=?", (doc_id,))
    conn.commit()
    conn.close()
    idx = get_index()
    with _index_lock:
        idx.remove_ids(np.asarray([doc_id], dtype=np.int64))
    save_index()
    get_bm25().remove(doc_id)
    return True


def update_memory(doc_id, payload):
    allowed = ("content", "type", "domain", "scope", "importance", "keywords", "persona_id")
    payload = dict(payload or {})
    clean, records = _sanitize_text_fields(payload, ("content", "keywords"), resource_id=doc_id, memory_id=doc_id)
    fields = {k: clean[k] for k in allowed if k in clean}
    if not fields:
        return False
    conn = get_conn()
    row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        conn.close()
        return False
    if records:
        fields["has_sensitive"] = 1
        fields["sensitive_types"] = json.dumps(sensitivity_types([m for _, _, _, ms in records for m in ms]), ensure_ascii=False)
    elif any(key in fields for key in ("content", "keywords")):
        fields["has_sensitive"] = 0
        fields["sensitive_types"] = "[]"
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [doc_id]
    conn.execute(f"UPDATE documents SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()
    for field, original, redacted, matches in records:
        _record_protected(doc_id, "memory", doc_id, field, original, redacted, matches, payload)
    if "content" in fields:
        content = str(fields["content"])
        vecs = embed_texts([content])
        idx = get_index()
        with _index_lock:
            idx.remove_ids(np.asarray([doc_id], dtype=np.int64))
            idx.add_with_ids(normalize(vecs[0]).reshape(1, -1), np.asarray([doc_id], dtype=np.int64))
        save_index()
        bm = get_bm25()
        bm.remove(doc_id)
        bm.add(doc_id, content + " " + str(fields.get("keywords") or ""))
    return True


def _process_attachments(mid, item):
    """处理记忆的附带内容：原子拆分 + 实体节点。"""
    for a in item.get("atoms") or []:
        if isinstance(a, dict) and (a.get("content") or "").strip():
            try:
                add_atom(mid, a)
            except Exception:
                pass
    for ent in item.get("entities") or []:
        if isinstance(ent, dict):
            try:
                add_entity(mid, ent)
            except Exception:
                pass
    for rel in item.get("relations") or []:
        if isinstance(rel, dict):
            try:
                add_relation(mid, rel)
            except Exception:
                pass


def add_batch(items):
    results = []
    for item in items:
        try:
            r = add_memory(item)
            _process_attachments(r.get("id"), item)
            results.append(r)
        except Exception as e:
            redacted_error, _ = redact_text(str(item.get("content", ""))[:80])
            results.append({"error": redact_text(str(e))[0], "content": redacted_error})
    return {"added": results}


def get_card(workspace_id):
    conn = get_conn()
    r = conn.execute(
        "SELECT * FROM workspace_cards WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    conn.close()
    if r is None:
        return None
    d = dict(r)
    for key in ("key_decisions", "in_progress", "next_steps"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except Exception:
            d[key] = []
    conn = get_conn()
    protected = conn.execute(
        "SELECT id FROM protected_sources WHERE resource_type='card' AND resource_id=? ORDER BY id",
        (str(workspace_id),),
    ).fetchall()
    conn.close()
    d["sensitive"] = bool(protected)
    d["protected_source_ids"] = [row["id"] for row in protected]
    return d


def get_setting(key):
    conn = get_conn()
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if r is None:
        return None
    try:
        return json.loads(r["value"])
    except Exception:
        return r["value"]


def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), time.time()),
    )
    conn.commit()
    conn.close()
    return {"key": key, "value": value}


# ---------------------------------------------------------------- sensitive content

def sensitive_config(key, default):
    """Read sensitive.* settings while keeping secure defaults on bad values."""
    value = get_setting("deepmemory.sensitive." + key)
    if value is None:
        return default
    try:
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int):
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def _audit(event, source=None, user_id="", session_id="", details=None):
    source = source or {}
    conn = get_conn()
    conn.execute(
        "INSERT INTO sensitive_audit (event, protected_source_id, memory_id,"
        " resource_type, resource_id, user_id, session_id, sensitivity_types,"
        " details, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            event,
            source.get("id"),
            source.get("memory_id"),
            source.get("resource_type") or "",
            str(source.get("resource_id") or ""),
            user_id or "",
            session_id or "",
            source.get("sensitivity_types") or "[]",
            json.dumps(details or {}, ensure_ascii=False),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def _record_protected(memory_id, resource_type, resource_id, field_name,
                      original, redacted, matches, payload=None):
    """Store an original only in the protected vault and audit its redaction."""
    if not matches:
        return None
    payload = payload or {}
    types = json.dumps(sensitivity_types(matches), ensure_ascii=False)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO protected_sources (memory_id, resource_type, resource_id,"
        " field_name, content, redacted_content, sensitivity_types, owner_user_id,"
        " owner_session_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            memory_id,
            resource_type,
            str(resource_id) if resource_id is not None else "",
            field_name,
            str(original),
            str(redacted),
            types,
            str(payload.get("user_id") or ""),
            str(payload.get("session_id") or ""),
            time.time(),
        ),
    )
    source_id = cur.lastrowid
    conn.commit()
    source = {
        "id": source_id,
        "memory_id": memory_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "sensitivity_types": types,
    }
    _audit(
        "write_redacted",
        source,
        payload.get("user_id"),
        payload.get("session_id"),
        {"field": field_name, "match_count": len(matches)},
    )
    return source_id


def _sanitize_text_fields(payload, fields, resource_type="memory", resource_id=None,
                          memory_id=None):
    """Redact text fields and return protected-vault records to create later."""
    clean = dict(payload)
    records = []
    for field in fields:
        if field not in clean or clean[field] is None:
            continue
        original = clean[field]
        if isinstance(original, str):
            redacted, matches = redact_text(original)
            clean[field] = redacted
        else:
            original_json = json.dumps(original, ensure_ascii=False)
            redacted_json, matches = redact_text(original_json)
            try:
                clean[field] = json.loads(redacted_json)
            except (TypeError, ValueError):
                clean[field] = redacted_json
        if matches:
            records.append((field, original, clean[field], matches))
    return clean, records


def _mark_sensitive(obj, records):
    obj["has_sensitive"] = bool(records)
    obj["sensitive_types"] = sensitivity_types([m for _, _, _, matches in records for m in matches])
    return obj


def _approval_hash(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def grant_sensitive_approval(user_id, session_id, confirmed):
    if not confirmed:
        raise ValueError("explicit user confirmation is required")
    if not user_id or not session_id:
        raise ValueError("user_id and session_id are required")
    now = time.time()
    ttl_minutes = max(1.0, min(30.0, float(sensitive_config("approval_ttl_minutes", 30))))
    ttl = ttl_minutes * 60.0
    max_uses = max(1, min(3, int(sensitive_config("approval_max_uses", 3))))
    token = secrets.token_urlsafe(32)
    conn = get_conn()
    conn.execute(
        "INSERT INTO sensitive_approvals (token_hash, user_id, session_id, granted_at,"
        " expires_at, max_uses, use_count) VALUES (?,?,?,?,?,?,0)",
        (_approval_hash(token), str(user_id), str(session_id), now, now + ttl, max_uses),
    )
    conn.commit()
    conn.close()
    _audit("approval_granted", user_id=user_id, session_id=session_id,
           details={"max_uses": max_uses, "ttl_minutes": ttl / 60.0})
    return {"approval_token": token, "expires_at": now + ttl, "max_uses": max_uses}


def end_sensitive_session(session_id):
    if not session_id:
        return 0
    conn = get_conn()
    cur = conn.execute(
        "UPDATE sensitive_approvals SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL",
        (time.time(), str(session_id)),
    )
    conn.commit()
    conn.close()
    _audit("session_ended", session_id=session_id, details={"revoked": cur.rowcount})
    return cur.rowcount


def _load_protected(source_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM protected_sources WHERE id=?", (int(source_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def expand_sensitive_source(source_id, token, user_id, session_id):
    source = _load_protected(source_id)
    if source is None:
        raise LookupError("protected source not found")
    if source.get("owner_user_id") and source["owner_user_id"] != str(user_id or ""):
        _audit("expand_denied", source, user_id, session_id, {"reason": "source_owner_mismatch"})
        raise PermissionError("protected source belongs to another user")
    now = time.time()
    conn = get_conn()
    approval = conn.execute(
        "SELECT * FROM sensitive_approvals WHERE token_hash=? AND user_id=? AND session_id=?",
        (_approval_hash(token), str(user_id or ""), str(session_id or "")),
    ).fetchone()
    allowed = bool(approval and not approval["revoked_at"] and approval["expires_at"] > now and approval["use_count"] < approval["max_uses"])
    if allowed:
        cur = conn.execute(
            "UPDATE sensitive_approvals SET use_count=use_count+1 WHERE id=? AND use_count < max_uses"
            " AND revoked_at IS NULL AND expires_at>?",
            (approval["id"], now),
        )
        allowed = cur.rowcount == 1
    conn.commit()
    conn.close()
    if not allowed:
        _audit("expand_denied", source, user_id, session_id, {"reason": "invalid_or_expired_approval"})
        raise PermissionError("valid approval is required")
    _audit("source_expanded", source, user_id, session_id, {"use": int(approval["use_count"]) + 1})
    return {"id": source["id"], "memory_id": source["memory_id"], "resource_type": source["resource_type"],
            "resource_id": source["resource_id"], "field_name": source["field_name"],
            "content": source["content"], "sensitivity_types": json.loads(source["sensitivity_types"] or "[]")}


def list_sensitive_audit(limit=100):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sensitive_audit ORDER BY id DESC LIMIT ?", (min(int(limit), 1000),)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- config center

CONFIG_SCHEMA_PATH = os.path.join(BASE_DIR, "config_schema.json")


def get_config_schema():
    try:
        with open(CONFIG_SCHEMA_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def get_config_values(include_secrets=False):
    conn = get_conn()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'deepmemory.%'"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        key = r["key"][len("deepmemory."):]
        if not include_secrets and key in SECRET_CONFIG_KEYS:
            continue
        try:
            out[key] = json.loads(r["value"])
        except Exception:
            out[key] = r["value"]
    return out


def set_config_values(payload):
    count = 0
    changed_embedding = any(str(k).startswith("embedding.") for k in payload)
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        set_setting("deepmemory." + key, value)
        count += 1
    if changed_embedding:
        # 嵌入配置变更：清空索引缓存并删除维度元数据，
        # 下次访问按新配置自动确定维度并重建索引
        global _index
        with _index_lock:
            _index = None
        try:
            os.remove(DIM_PATH)
        except FileNotFoundError:
            pass
    return {"saved": count}


def get_session_config(session_id):
    """Get effective config for a session: session overrides merged over defaults."""
    defaults = get_config_values()
    if not session_id:
        return defaults
    conn = get_conn()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?",
        (f"deepmemory.session.{session_id}.%",)
    ).fetchall()
    conn.close()
    overrides = {}
    prefix = f"deepmemory.session.{session_id}."
    for r in rows:
        key = r["key"][len(prefix):]
        if key in SECRET_CONFIG_KEYS:
            continue
        try:
            overrides[key] = json.loads(r["value"])
        except Exception:
            overrides[key] = r["value"]
    result = dict(defaults)
    result.update(overrides)
    return result


def set_session_config(session_id, key, value):
    """Set a single session config override."""
    if not session_id or not key:
        raise ValueError("session_id and key required")
    set_setting(f"deepmemory.session.{session_id}.{key}", value)
    return {"key": key, "value": value}


def reset_session_config_key(session_id, key):
    """Remove a session override, reverting to default."""
    if not session_id or not key:
        raise ValueError("session_id and key required")
    conn = get_conn()
    conn.execute(
        "DELETE FROM settings WHERE key=?",
        (f"deepmemory.session.{session_id}.{key}",)
    )
    conn.commit()
    conn.close()
    return {"key": key, "reset": True}


def list_session_overrides(session_id):
    """List keys that have session-specific overrides."""
    if not session_id:
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT key FROM settings WHERE key LIKE ?",
        (f"deepmemory.session.{session_id}.%",)
    ).fetchall()
    conn.close()
    prefix = f"deepmemory.session.{session_id}."
    return [r["key"][len(prefix):] for r in rows]


# ---------------------------------------------------------------- lifecycle

DECAY_PROTECTED = 0.85      # importance above this is exempt from decay
DECAY_CLEANUP_IMPORTANCE = 0.1
DECAY_CLEANUP_DAYS = 30.0
DECAY_LAST_KEY = "last_decay_at"
DECAY_INTERVAL_HOURS = 24.0
_decay_lock = threading.Lock()


def decay_interval_hours():
    return min(
        8760.0,
        max(0.01, cfg_float("importance_decay.interval_hours", DECAY_INTERVAL_HOURS)),
    )


def count_active():
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE status='active'"
    ).fetchone()["c"]
    conn.close()
    return n


def _run_decay(decay_rate=0.01, force=False):
    """Daily importance decay with access reinforcement (AstrBot-aligned).

    Memories accessed recently decay less: reference timestamp =
    created + reference_weight*(last_access - created), so with weight 1.0
    the reference equals max(created, last_access). High-importance memories
    are protected. Old low-importance memories move to `archived` (soft delete).
    """
    # 配置中心覆盖：deepmemory.decay_rate
    cfg_rate = get_setting("deepmemory.decay_rate")
    if cfg_rate is not None:
        try:
            decay_rate = float(cfg_rate)
        except Exception:
            pass
    if not cfg_bool("access_reinforcement.enabled", True):
        decay_rate = max(0.0, decay_rate)
    interval_hours = decay_interval_hours()
    interval_seconds = interval_hours * 3600.0
    now = time.time()
    last = get_setting(DECAY_LAST_KEY)
    last = float(last) if last else 0.0
    if not force and now - last < interval_seconds:
        return {
            "skipped": True,
            "interval_hours": interval_hours,
            "next_run_in_seconds": int(interval_seconds - (now - last)),
            "documents": count_active(),
        }
    protect = cfg_float("access_reinforcement.protect_importance", DECAY_PROTECTED)
    # 清理参数主读 archiving 组（配置页即改即生效），access_reinforcement.cleanup_* 为兼容回退
    auto_archive = cfg_bool("archiving.auto_archive_enabled", True)
    cleanup_imp = cfg_float(
        "archiving.archive_importance_threshold",
        cfg_float("access_reinforcement.cleanup_importance", DECAY_CLEANUP_IMPORTANCE),
    )
    cleanup_days = cfg_float(
        "archiving.archive_days_threshold",
        cfg_float("access_reinforcement.cleanup_days", DECAY_CLEANUP_DAYS),
    )
    ref_weight = min(1.0, max(0.0, cfg_float("access_reinforcement.reference_weight", 1.0)))
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, importance, created_at, last_access_at FROM documents WHERE status='active'"
    ).fetchall()
    decayed = 0
    archived_ids = []
    for r in rows:
        imp = float(r["importance"] or 0.5)
        if imp >= protect:
            continue
        created = float(r["created_at"] or now)
        accessed = float(r["last_access_at"] or now)
        ref = created + ref_weight * (accessed - created)
        days = max(0.0, (now - ref) / 86400.0)
        if days <= 0:
            continue
        new_imp = imp * math.exp(-decay_rate * days)
        conn.execute("UPDATE documents SET importance=? WHERE id=?", (round(new_imp, 4), r["id"]))
        decayed += 1
        age_days = (now - created) / 86400.0
        if auto_archive and new_imp < cleanup_imp and age_days > cleanup_days:
            archived_ids.append(r["id"])
    for mid in archived_ids:
        conn.execute("UPDATE documents SET status='archived' WHERE id=?", (mid,))
        get_bm25().remove(mid)
    # expire atoms past their TTL
    conn.execute(
        "UPDATE atoms SET status='expired' WHERE status='active' AND expires_at>0 AND expires_at<?",
        (now,),
    )
    conn.commit()
    conn.close()
    set_setting(DECAY_LAST_KEY, now)
    return {
        "decayed": decayed,
        "archived": len(archived_ids),
        "documents": count_active(),
        "interval_hours": interval_hours,
    }


def run_decay(decay_rate=0.01, force=False):
    with _decay_lock:
        return _run_decay(decay_rate, force)


def _nightly_tier_forgetting():
    """夜间遗忘：importance 低于阈值的 active 记忆降 cold；冷置 30 天以上转 archive（收敛检索面）。"""
    conn = get_conn()
    now = time.time()
    cold_threshold = float(cfg_float("archiving.cold_importance_threshold", 0.4))
    archive_after_days = float(cfg_float("archiving.archive_after_days", 30))
    moved_cold = conn.execute(
        "UPDATE documents SET storage_tier='cold', memory_class='short_term', updated_at=?"
        " WHERE status='active' AND storage_tier='active' AND importance<? AND COALESCE(rule_crystallized,0)=0",
        (now, cold_threshold),
    ).rowcount
    conn.commit()
    # active 冷置太久的（access 强化过的 last_access_at 推后）
    moved_archive = conn.execute(
        "UPDATE documents SET storage_tier='archive', memory_class='compressed_archive', updated_at=?"
        " WHERE status='active' AND storage_tier='cold' AND ?-COALESCE(last_access_at, created_at) > ?",
        (now, now, archive_after_days * 86400),
    ).rowcount
    conn.commit()
    conn.close()
    rebuild_bm25()
    return {"moved_cold": moved_cold, "moved_archive": moved_archive}


def _nightly_maintenance():
    """夜间批处理（默认 03:00，谷时低负载）：遗忘 + 沉淀(LLM 提炼) + 固化(LLM 规则)。"""
    out = {"forgetting": _nightly_tier_forgetting()}
    try:
        if cfg_bool("memory_consolidation.enabled", True) and cfg_bool("memory_consolidation.llm_summarize", True):
            res = consolidate_memories(similarity=cfg_float("memory_consolidation.semantic_similarity_threshold", 0.92),
                                       limit_groups=5, dry_run=False)
            out["consolidate"] = res if isinstance(res, dict) else {"ok": bool(res)}
    except Exception as exc:
        out["consolidate_error"] = redact_text(str(exc))[0]
    try:
        cands = rule_candidates(min_similar=0.86, min_occur=2, limit=5)
        if cands.get("candidates"):
            # LLM 提炼规则（低成本模型），失败则直接按代表记忆固化
            texts = "\n---\n".join(str(c["representative"]["content"])[:300] for c in cands["candidates"])
            merged_ids = [r["id"] for c in cands["candidates"] for r in c["similar_ids"]]
            summary = llm_chat(
                "以下是用户在对话中被反复确认的偏好/约定（重复出现）。请提炼为一条简洁、可执行的持久规则文本（中文，30字内）：\n" + texts,
                system="你是记忆固化助手：输出仅规则本身，不要解释。")
            if summary.get("content") and not summary.get("error"):
                out["crystallize_note"] = summary["content"]
            out["crystallize"] = crystallize_rules(merged_ids)
    except Exception as exc:
        out["crystallize_error"] = redact_text(str(exc))[0]
    return out


def lifecycle_scheduler(stop_event, check_interval=3600):
    """每日 decay（每小时）+ 凌晨 03:00 夜间批处理（遗忘/沉淀/固化，谷时低成本模型）。"""
    last_night = None
    while not stop_event.is_set():
        wait_seconds = check_interval
        try:
            result = run_decay()
            if not result.get("skipped"):
                print("memory lifecycle decay: " + json.dumps(result, ensure_ascii=False), flush=True)
            else:
                wait_seconds = min(
                    check_interval,
                    max(1, int(result.get("next_run_in_seconds") or check_interval)),
                )
            now_h = int(time.strftime("%H"))
            today = time.strftime("%Y-%m-%d")
            if now_h == 3 and last_night != today:
                last_night = today
                print("memory nightly maintenance: " + json.dumps(_nightly_maintenance(), ensure_ascii=False)[:400], flush=True)
        except Exception as exc:
            print("memory lifecycle decay failed: " + redact_text(str(exc))[0], flush=True)
        stop_event.wait(wait_seconds)


def archive_memories(ids):
    now = time.time()
    conn = get_conn()
    done = 0
    for mid in ids:
        cur = conn.execute(
            "UPDATE documents SET status='archived', last_access_at=? WHERE id=? AND status='active'",
            (now, int(mid)),
        )
        if cur.rowcount:
            done += 1
            get_bm25().remove(int(mid))
    conn.commit()
    conn.close()
    return {"archived": done}


def restore_memory(mid):
    now = time.time()
    conn = get_conn()
    r = conn.execute(
        "SELECT id, content, key_facts, keywords FROM documents WHERE id=? AND status='archived'",
        (int(mid),),
    ).fetchone()
    if r is None:
        conn.close()
        return False
    conn.execute(
        "UPDATE documents SET status='active', last_access_at=? WHERE id=?", (now, int(mid))
    )
    conn.commit()
    conn.close()
    text = " ".join(x for x in (r["content"], r["key_facts"], r["keywords"]) if x)
    get_bm25().add(r["id"], text)
    return True


def create_backup():
    """快照备份：SQLite 在线备份 + FAISS 索引文件 + 清单。"""
    name = "backup-" + time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, name)
    os.makedirs(dest, exist_ok=True)
    conn = get_conn()
    try:
        bconn = sqlite3.connect(os.path.join(dest, "memory.db"))
        conn.backup(bconn)
        bconn.close()
    finally:
        conn.close()
    if os.path.exists(INDEX_PATH):
        shutil.copy2(INDEX_PATH, os.path.join(dest, "memory.faiss"))
    docs = count_active()
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "created_at": time.time(), "documents": docs}, fh, ensure_ascii=False)
    return {"name": name, "documents": docs}


def list_backups():
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for n in sorted(os.listdir(BACKUP_DIR), reverse=True):
        mf = os.path.join(BACKUP_DIR, n, "manifest.json")
        if os.path.isfile(mf):
            try:
                out.append(json.load(open(mf, encoding="utf-8")))
            except Exception:
                continue
    return out


def restore_backup(name):
    safe = os.path.basename(name)
    src = os.path.join(BACKUP_DIR, safe)
    if not os.path.isfile(os.path.join(src, "memory.db")):
        return False
    global _index
    with _index_lock:
        shutil.copy2(os.path.join(src, "memory.db"), DB_PATH)
        if os.path.isfile(os.path.join(src, "memory.faiss")):
            shutil.copy2(os.path.join(src, "memory.faiss"), INDEX_PATH)
        else:
            if os.path.exists(INDEX_PATH):
                os.remove(INDEX_PATH)
        _index = None
        rebuild_bm25()
    return True


def _rebuild_indexes_internal():
    """影子重建核心（不依赖 get_index，可安全用于维度迁移）。"""
    old_ntotal = 0
    if os.path.exists(INDEX_PATH):
        try:
            old_ntotal = faiss.read_index(INDEX_PATH).ntotal
        except Exception:
            old_ntotal = 0
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, key_facts FROM documents WHERE status='active'"
    ).fetchall()
    conn.close()
    doc_ids = [r["id"] for r in rows]
    fingerprint = {"count": len(doc_ids), "max_id": max(doc_ids) if doc_ids else 0}
    texts = [str(r["content"]) + " " + str(r["key_facts"] or "") for r in rows]
    if texts:
        vecs = embed_texts(texts)
        mat = np.vstack([normalize(v) for v in vecs]).astype(np.float32)
        ids = np.asarray(doc_ids, dtype=np.int64)
        d = mat.shape[1]
        tmp = faiss.IndexIDMap(faiss.IndexFlatL2(d))
        tmp.add_with_ids(mat, ids)
    else:
        # 无文档也要按当前配置确定维度（空库切换 embedding 场景）
        vecs = embed_texts(["warmup"])
        d = vecs[0].shape[0] if vecs else DIM
        tmp = faiss.IndexIDMap(faiss.IndexFlatL2(d))
    shadow_path = INDEX_PATH + ".shadow"
    faiss.write_index(tmp, shadow_path)
    os.replace(shadow_path, INDEX_PATH)
    global _index
    with _index_lock:
        _index = None
    rebuild_bm25()
    return {
        "rebuilt": True,
        "fingerprint": fingerprint,
        "index_before": old_ntotal,
        "index_after": tmp.ntotal,
    }


def rebuild_indexes():
    """索引重建：指纹校验 + 影子重建（临时文件生成后原子替换，living memory 对齐）。"""
    return _rebuild_indexes_internal()


def rule_candidates(min_similar=0.86, min_occur=2, limit=20):
    """L0 固化候选（E1）：type=preference 记忆中语义相似 （重复抽取的同约定）>=min_occur 次 -> 候选。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, importance, workspace_id FROM documents"
        " WHERE status='active' AND type='preference' AND COALESCE(rule_crystallized,0)=0"
        " ORDER BY importance DESC").fetchall()
    conn.close()
    if not rows:
        return {"candidates": [], "note": "no preference memories"}
    cands = []
    for i, a in enumerate(rows):
        va = embed_texts([str(a["content"])[:200]])
        if not va:
            continue
        group = [a]
        for b in rows[i + 1:]:
            vb = embed_texts([str(b["content"])[:200]])
            if not vb:
                continue
            sim = float(np.dot(normalize(va[0]), normalize(vb[0])))
            if sim >= min_similar:
                group.append(b)
        if len(group) >= min_occur:
            cands.append({
                "representative": dict(group[0]),
                "occurrences": len(group),
                "similar_ids": [g["id"] for g in group],
            })
        if len(cands) >= limit:
            break
    return {"candidates": cands}


def crystallize_rules(ids, rules_dir=None):
    """E2 固化：候选沉淀规则文件（带 provenance），标记 rule_crystallized=1（注入池移除）。"""
    rules_dir = rules_dir or os.path.join(DATA_DIR, "rules")
    os.makedirs(rules_dir, exist_ok=True)
    conn = get_conn()
    markers = []
    cnt = 0
    for mid in ids:
        try:
            mid = int(mid)
        except (TypeError, ValueError):
            continue
        rows = conn.execute(
            "SELECT id, content, importance, workspace_id, created_at FROM documents WHERE id=?",
            (mid,),
        ).fetchall()
        if not rows:
            continue
        r = rows[0]
        markers.append("- " + str(r["content"])[:400] + " (mem #" + str(r["id"]) +
                       " / workspace=" + str(r["workspace_id"]) + " / " + str(r["created_at"]) + ")")
        conn.execute("UPDATE documents SET rule_crystallized=1 WHERE id=?", (mid,))
        cnt += 1
    conn.commit()
    conn.close()
    out = os.path.join(rules_dir, "crystallized-rules.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("# 固化规则 L0 沉淀（runtime 生成）\n\n")
        fh.write("> 重复出现的高重要度偏好记忆固化；rule_crystallized=1 不再注入，原文保留可溯源。\n\n")
        fh.write("\n".join(markers) + "\n")
    return {"crystallized": cnt, "file": out}


def consolidate_memories(similarity=None, limit_groups=None, dry_run=False):
    """记忆整合（living memory 对齐）：语义聚类高度相似的活跃记忆，组内保留
    importance 最高者为主记忆，其余并入 canonical_summary 后归档。

    参数读 memory_consolidation 配置组；dry_run=True 只返回候选组不落库
    （供 LLM 摘要流：先 candidates 拿组 -> 模型生成摘要 -> apply 应用）。
    """
    threshold = (
        similarity
        if similarity is not None
        else cfg_float("memory_consolidation.semantic_similarity_threshold", 0.92)
    )
    min_group = int(cfg_float("memory_consolidation.min_memories_per_group", 2))
    min_age_days = cfg_float("memory_consolidation.min_age_days", 1.0)
    max_importance = cfg_float("memory_consolidation.max_importance", 0.5)
    max_groups = int(limit_groups if limit_groups is not None else cfg_float("memory_consolidation.max_groups_per_run", 5))
    now = time.time()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM documents WHERE status='active' ORDER BY importance DESC, created_at ASC LIMIT 500"
    ).fetchall()
    conn.close()
    # 过滤：只整合低重要度且足够老的记忆
    rows = [
        r
        for r in rows
        if float(r["importance"] or 0.5) <= max_importance
        and (now - float(r["created_at"] or now)) / 86400.0 >= min_age_days
    ]
    if len(rows) < min_group:
        return {"merged": 0, "groups": 0, "threshold": threshold, "candidates": []}
    texts = [str(r["content"]) + " " + str(r["key_facts"] or "") for r in rows]
    try:
        vecs = embed_texts(texts)
    except Exception:
        return {"merged": 0, "groups": 0, "threshold": threshold, "candidates": [], "error": "embedding failed"}
    used = set()
    groups = []
    n = len(rows)
    for i in range(n):
        if rows[i]["id"] in used:
            continue
        v = normalize(vecs[i])
        group = [rows[i]]
        for j in range(i + 1, n):
            if rows[j]["id"] in used:
                continue
            if float(np.dot(v, normalize(vecs[j]))) >= threshold:
                group.append(rows[j])
        if len(group) >= min_group:
            used.update(g["id"] for g in group)
            groups.append(group)
        if len(groups) >= max_groups:
            break
    candidates = []
    merged = 0
    conn = get_conn()
    for group in groups:
        primary = max(group, key=lambda g: (float(g["importance"] or 0.5), -float(g["created_at"] or 0)))
        rest = [g for g in group if g["id"] != primary["id"]]
        if not rest:
            continue
        candidates.append({
            "primary_id": primary["id"],
            "primary_content": str(primary["content"])[:200],
            "archived_ids": [g["id"] for g in rest],
            "contents": [str(g["content"])[:200] for g in rest],
        })
        if dry_run:
            continue
        extras = "\n".join(str(g["content"])[:300] for g in rest)
        existing = str(primary["canonical_summary"] or "")
        canonical = (existing + ("\n" if existing else "") + extras)[:4000]
        conn.execute(
            "UPDATE documents SET canonical_summary=? WHERE id=?", (canonical, primary["id"])
        )
        for g in rest:
            conn.execute("UPDATE documents SET status='archived' WHERE id=?", (g["id"],))
            get_bm25().remove(g["id"])
        merged += len(rest)
    conn.commit()
    conn.close()
    return {
        "merged": merged,
        "groups": len(candidates),
        "threshold": threshold,
        "candidates": candidates,
    }


def apply_consolidation(groups):
    """应用 LLM 摘要后的整合结果：{primary_id, archived_ids, canonical_summary?}。

    canonical_summary 缺省时用被合并记忆原文拼接（无 LLM 回退）。
    """
    conn = get_conn()
    done = 0
    protected_records = []
    for g in groups or []:
        pid = int(g.get("primary_id"))
        archived = [int(x) for x in (g.get("archived_ids") or [])]
        original_summary = str(g.get("canonical_summary") or "").strip()
        summary, summary_matches = redact_text(original_summary)
        row = conn.execute(
            "SELECT canonical_summary FROM documents WHERE id=? AND status='active'", (pid,)
        ).fetchone()
        if row is None:
            continue
        if not summary:
            rows = conn.execute(
                f"SELECT content FROM documents WHERE id IN ({','.join('?' * len(archived))})",
                archived,
            ).fetchall() if archived else []
            summary = "\n".join(str(r["content"])[:300] for r in rows)
        existing = str(row["canonical_summary"] or "")
        conn.execute(
            "UPDATE documents SET canonical_summary=?, has_sensitive=CASE WHEN ? THEN 1 ELSE has_sensitive END,"
            " sensitive_types=CASE WHEN ? THEN ? ELSE sensitive_types END WHERE id=?",
            ((existing + ("\n" if existing else "") + summary)[:4000], bool(summary_matches),
             bool(summary_matches), json.dumps(sensitivity_types(summary_matches), ensure_ascii=False), pid),
        )
        if summary_matches:
            protected_records.append((pid, original_summary, summary, summary_matches, g))
        for mid in archived:
            conn.execute("UPDATE documents SET status='archived' WHERE id=?", (mid,))
            get_bm25().remove(mid)
        done += len(archived)
    conn.commit()
    conn.close()
    for pid, original, redacted, matches, payload in protected_records:
        _record_protected(pid, "memory", pid, "canonical_summary", original, redacted, matches, payload)
    return {"merged": done, "groups": len(groups or [])}


def get_overview(workspace_id=None, session_id=None):
    conn = get_conn()
    mems = [
        dict(r)
        for r in conn.execute(
        "SELECT id, content, type, domain, scope, importance, created_at, has_sensitive,"
            " last_access_at, access_count, status, keywords FROM documents"
            " WHERE status='active' ORDER BY id DESC LIMIT 300"
        ).fetchall()
    ]
    active_n = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE status='active'"
    ).fetchone()["c"]
    archived_n = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE status='archived'"
    ).fetchone()["c"]
    atoms_n = conn.execute("SELECT COUNT(*) c FROM atoms").fetchone()["c"]
    conn.close()
    for item in mems:
        if item.get("has_sensitive"):
            item["content"] = redact_text(str(item.get("content") or ""))[0]
    card = get_card(workspace_id) if workspace_id else None
    enabled = True
    if session_id:
        v = get_setting("session_enabled:" + session_id)
        enabled = True if v is None else bool(v)
    return {
        "memories": mems,
        "documents": active_n,
        "archived": archived_n,
        "atoms": atoms_n,
        "card": card,
        "session_enabled": enabled,
    }


def put_card(workspace_id, payload):
    payload = dict(payload or {})
    clean, records = _sanitize_text_fields(
        payload, ("goal", "current_plan", "key_decisions", "in_progress", "next_steps"),
        resource_type="card", resource_id=workspace_id,
    )
    now = time.time()
    conn = get_conn()
    old = conn.execute(
        "SELECT version FROM workspace_cards WHERE workspace_id=?", (workspace_id,)
    ).fetchone()
    version = (old["version"] if old else 0) + 1
    conn.execute(
        "INSERT INTO workspace_cards (workspace_id, goal, current_plan, key_decisions,"
        " in_progress, next_steps, version, updated_at) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(workspace_id) DO UPDATE SET goal=excluded.goal,"
        " current_plan=excluded.current_plan, key_decisions=excluded.key_decisions,"
        " in_progress=excluded.in_progress, next_steps=excluded.next_steps,"
        " version=excluded.version, updated_at=excluded.updated_at",
        (
            workspace_id,
            clean.get("goal") or "",
            clean.get("current_plan") or "",
            json.dumps(clean.get("key_decisions") or [], ensure_ascii=False),
            json.dumps(clean.get("in_progress") or [], ensure_ascii=False),
            json.dumps(clean.get("next_steps") or [], ensure_ascii=False),
            version,
            now,
        ),
    )
    conn.commit()
    conn.close()
    protected_ids = []
    for field, original, redacted, matches in records:
        protected_ids.append(_record_protected(None, "card", workspace_id, field, original, redacted, matches, payload))
    return {"workspace_id": workspace_id, "version": version, "sensitive": bool(records),
            "protected_source_ids": protected_ids}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "memory-server/0.1"

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _reject_browser_origin(self):
        if self.headers.get("Origin"):
            self._send(403, {"error": "browser origin is not allowed"})
            return True
        token = ""
        try:
            with open(API_TOKEN_FILE, encoding="utf-8") as fh:
                token = fh.read().strip()
        except FileNotFoundError:
            pass
        if token and self.headers.get("Authorization") != "Bearer " + token:
            self._send(401, {"error": "bearer token required"})
            return True
        return False

    def do_OPTIONS(self):
        self._send(403, {"error": "browser origin is not allowed"})

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise OverflowError("request body too large")
        if length <= 0:
            return {}
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        return json.loads(self.rfile.read(length))

    @staticmethod
    def _v2_times(value):
        """Expose v2 timestamps consistently as Beijing (+08:00) ISO strings."""
        if isinstance(value, dict):
            return {key: Handler._v2_times(item) for key, item in value.items()}
        if isinstance(value, list):
            return [Handler._v2_times(item) for item in value]
        return value

    @staticmethod
    def _v2_obj(value):
        time_keys = {
            "created_at", "updated_at", "cold_at", "compressed_at", "lifecycle_expires_at",
            "event_time_start", "event_time_end", "period_start", "period_end",
        }
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key in time_keys and isinstance(item, (int, float)) and item:
                    item = datetime.fromtimestamp(item, ZoneInfo("Asia/Shanghai")).isoformat()
                out[key] = Handler._v2_obj(item)
            return out
        if isinstance(value, list):
            return [Handler._v2_obj(item) for item in value]
        return value

    def _v2_call(self, operation):
        try:
            return self._send(200, self._v2_obj(operation()))
        except NotFoundError as exc:
            return self._send(404, {"error": str(exc)})
        except ConflictError as exc:
            return self._send(409, {"error": str(exc)})
        except PermissionDenied as exc:
            return self._send(403, {"error": str(exc)})
        except (DomainError, InvalidTransition, ValueError, KeyError, TypeError) as exc:
            return self._send(400, {"error": str(exc)})

    def _v2_store(self):
        store = V2Store(DB_PATH)
        store.migrate()
        return store

    @staticmethod
    def _v2_changes(changes):
        changes = dict(changes or {})
        time_keys = {"event_time_start", "event_time_end", "lifecycle_expires_at", "cold_at", "compressed_at"}
        for key in time_keys:
            value = changes.get(key)
            if isinstance(value, str) and value.strip():
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                changes[key] = parsed.timestamp()
        return changes

    def _v2_get(self, path, qs):
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if path == "/v1/v2/tasks":
            return self._v2_call(lambda: {"tasks": self._v2_store().list_tasks(
                workspace_id=qs.get("workspace_id", [None])[0],
                status=qs.get("status", [None])[0],
                parent_task_id=qs.get("parent_task_id", [None])[0],
                session_id=qs.get("session_id", [None])[0],
                limit=qs.get("limit", [100])[0],
            )})
        if len(parts) >= 4 and parts[2] == "tasks":
            task_id = parts[3]
            store = self._v2_store()
            if len(parts) == 5 and parts[4] == "history":
                return self._v2_call(lambda: {"task_id": task_id, "events": store.task_history(task_id)})
            return self._v2_call(lambda: {"task": store.get_task(task_id)})
        if len(parts) >= 5 and parts[2] == "cards":
            kind, card_key = parts[3], parts[4]
            store = self._v2_store()
            if len(parts) == 6 and parts[5] == "revisions":
                return self._v2_call(lambda: {"revisions": store.state_card_revisions(card_key, kind)})
            return self._v2_call(lambda: {"card": store.get_state_card(card_key, kind)})
        if len(parts) >= 4 and parts[2] == "session-keys":
            # GET /v1/v2/session-keys/<session_id>?workspace_id=
            # 返回描述符（prefix），全 key 通过 POST create/rotate 获得。
            store = self._v2_store()
            session_id = unquote(parts[3])
            workspace_id = qs.get("workspace_id", [""])[0] or ""
            descriptor = store.get_session_key(workspace_id, session_id, issue=False)
            if descriptor is None:
                return self._v2_call(lambda: {"key": None, "error": "no session key yet"})
            return self._v2_call(lambda: {"key": {
                "prefix": descriptor["key_prefix"],
                "session_id": descriptor["session_id"],
                "workspace_id": descriptor["workspace_id"],
                "has_full": False,
            }})
        if len(parts) >= 4 and parts[2] == "sessions" and len(parts) >= 6 and parts[4] == "memories" and parts[5] == "export":
            # GET /v1/v2/sessions/<session_id>/memories/export?key=...&include_sensitive=
            session_id = unquote(parts[3])
            key = qs.get("key", [""])[0] or ""
            include_sensitive = qs.get("include_sensitive", ["0"])[0] in ("1", "true")
            store = self._v2_store()
            workspace_id = ""
            if key:
                try:
                    workspace_id, resolved_sid = store.resolve_session_key(key)
                except Exception as exc:
                    return self._send(403, {"error": str(exc)})
                if resolved_sid != session_id:
                    return self._send(403, {"error": "key does not own this session"})
            else:
                workspace_id = qs.get("workspace_id", [""])[0] or ""
            return self._v2_call(lambda: store.export_session(workspace_id, session_id, include_sensitive))
        if path == "/v1/v2/recall":
            return self._send(405, {"error": "recall requires POST"})
        return None

    def do_GET(self):
        try:
            if self._reject_browser_origin():
                return
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
            if path.startswith("/v1/v2/"):
                result = self._v2_get(path, qs)
                return result if result is not None else self._send(404, {"error": "not found"})
            if path == "/v1/health":
                return self._send(
                    200,
                    {
                        "status": "ok",
                        "documents": get_index().ntotal,
                        "bm25_docs": len(get_bm25().docs),
                        "time": time.time(),
                    },
                )
            if path == "/v1/stats":
                conn = get_conn()
                n = conn.execute(
                    "SELECT COUNT(*) c FROM documents WHERE status='active'"
                ).fetchone()["c"]
                a = conn.execute("SELECT COUNT(*) c FROM atoms").fetchone()["c"]
                g = conn.execute("SELECT COUNT(*) c FROM graph_nodes").fetchone()["c"]
                e = conn.execute("SELECT COUNT(*) c FROM graph_edges").fetchone()["c"]
                conn.close()
                return self._send(
                    200, {"documents": n, "atoms": a, "graph_nodes": g, "graph_edges": e}
                )
            if path == "/v1/overview":
                return self._send(
                    200,
                    get_overview(
                        workspace_id=qs.get("workspace_id", [None])[0],
                        session_id=qs.get("session_id", [None])[0],
                    ),
                )
            if path == "/v1/config-schema":
                return self._send(200, {"schema": get_config_schema()})
            if path == "/v1/config":
                return self._send(200, {"config": get_config_values()})
            if path == "/v1/config/session":
                session_id = qs.get("session_id", [None])[0]
                if not session_id:
                    return self._send(400, {"error": "session_id required"})
                return self._send(200, {"config": get_session_config(session_id), "overrides": list_session_overrides(session_id)})
            if path == "/v1/sensitive/audit":
                return self._send(200, {"audit": list_sensitive_audit(int(qs.get("limit", ["100"])[0]))})
            if path.startswith("/v1/sensitive/sources/"):
                source = _load_protected(path.rsplit("/", 1)[-1])
                if source is None:
                    return self._send(404, {"error": "protected source not found"})
                return self._send(200, {"source": {"id": source["id"], "memory_id": source["memory_id"],
                    "resource_type": source["resource_type"], "resource_id": source["resource_id"],
                    "field_name": source["field_name"], "redacted_content": source["redacted_content"],
                    "sensitivity_types": json.loads(source["sensitivity_types"] or "[]")}})
            if path.startswith("/v1/settings/"):
                key = unquote(path[len("/v1/settings/"):])
                if key.startswith("deepmemory.") and key[len("deepmemory."):] in SECRET_CONFIG_KEYS:
                    return self._send(404, {"error": "setting not found"})
                return self._send(200, {"key": key, "value": get_setting(key)})
            if path.startswith("/v1/cards/"):
                wid = path.rsplit("/", 1)[-1]
                card = get_card(wid)
                return self._send(200, {"card": card})
            if path == "/v1/memories/list":
                conn = get_conn()
                sql = "SELECT * FROM documents WHERE 1=1"
                args = []
                for key, col in (
                    ("scope", "scope"),
                    ("workspace_id", "workspace_id"),
                    ("domain", "domain"),
                    ("status", "status"),
                    ("session_id", "session_id"),
                    ("persona_id", "persona_id"),
                ):
                    if qs.get(key):
                        sql += f" AND {col}=?"
                        args.append(qs[key][0])
                limit = int(qs.get("limit", ["100"])[0])
                sql += " ORDER BY id DESC LIMIT ?"
                args.append(min(limit, 1000))
                rows = conn.execute(sql, args).fetchall()
                conn.close()
                return self._send(200, {"memories": [dict(r) for r in rows]})
            if path == "/v1/memories/for-doc":
                doc_path = qs.get("path", [""])[0] or ""
                if not doc_path:
                    return self._send(400, {"error": "path is required"})
                return self._v2_call(lambda: {"doc_path": doc_path, "links": self._v2_store().documents_for_path(
                    doc_path, workspace_id=qs.get("workspace_id", [None])[0])})
            if path == "/v1/memories/libraries":
                return self._v2_call(lambda: {"libraries": self._v2_store().library_stats()})
            if path.startswith("/v1/info/"):
                # GET /v1/info/<domain>/<key> | /v1/info/keys/<domain> | /v1/info/domains
                iparts = [unquote(x) for x in path.strip("/").split("/")]
                if len(iparts) == 4 and iparts[1] == "info" and iparts[2] not in ("keys",):
                    # GET /v1/info/<domain>/<key>
                    entry = get_info_store().get(iparts[2], iparts[3])
                    if entry is None:
                        return self._send(404, {"error": "info not found（未登记键：先 POST /v1/info/<domain>/<key>）"})
                    return self._send(200, entry)
                if len(iparts) == 4 and iparts[1] == "info" and iparts[2] == "keys":
                    # GET /v1/info/keys/<domain>
                    return self._send(200, {"domain": iparts[3], "keys": get_info_store().keys(iparts[3])})
                if len(iparts) == 3 and iparts[1] == "info" and iparts[2] == "domains":
                    # GET /v1/info/domains
                    return self._send(200, {"domains": get_info_store().domains()})
                return self._send(404, {"error": "info route not found"})
            if path == "/v1/memories/injection-log":
                with _injection_lock:
                    return self._send(200, {"injection": _last_injection})
            if path == "/v1/topic-summaries":
                topic_id = qs.get("topic_id", [""])[0]
                conn = get_conn()
                rows = conn.execute(
                    "SELECT topic_id, seq, summary, start_time, end_time, prev_seq, created_at"
                    " FROM topic_summaries WHERE topic_id=? ORDER BY seq DESC LIMIT ?",
                    (topic_id, int(qs.get("limit", ["20"])[0])),
                ).fetchall()
                conn.close()
                return self._send(200, {"topic": topic_id, "summaries": [dict(r) for r in rows]})
            if path == "/v1/memories/hot":
                # 热点记忆：按调用次数（access_count）排序——用于可见化/维护，不进注入排序（防缓存抖动）
                limit = int(qs.get("limit", ["10"])[0])
                conn = get_conn()
                rows = conn.execute(
                    "SELECT id, content, type, importance, access_count, last_access_at,"
                    " storage_tier, library FROM documents WHERE status='active'"
                    " ORDER BY access_count DESC, last_access_at DESC LIMIT ?",
                    (min(limit, 100),),
                ).fetchall()
                conn.close()
                return self._send(200, {"hot": [dict(r) for r in rows]})
            if path.startswith("/v1/memories/"):
                parts = path.strip("/").split("/")
                doc_id = int(parts[-1] if parts[-1] != "source" else parts[-2])
                if parts[-1] == "source":
                    return self._send(200, {"sources": get_sources(doc_id)})
                try:
                    mem = self._v2_store().get_memory(doc_id)
                except NotFoundError:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"memory": mem})
            if path.startswith("/v1/atoms/list"):
                conn = get_conn()
                sql = "SELECT * FROM atoms WHERE 1=1"
                args = []
                if qs.get("memory_id"):
                    sql += " AND memory_id=?"
                    args.append(int(qs["memory_id"][0]))
                rows = conn.execute(sql + " ORDER BY id DESC LIMIT 500", args).fetchall()
                conn.close()
                return self._send(200, {"atoms": [dict(r) for r in rows]})
            if path.startswith("/v1/graph/memories"):
                entity = qs.get("entity", [""])[0]
                conn = get_conn()
                mem_ids = set()
                if entity:
                    nodes = conn.execute(
                        "SELECT id FROM graph_nodes WHERE name=?", (entity,)
                    ).fetchall()
                    node_ids = [n["id"] for n in nodes]
                    if node_ids:
                        qmarks = ",".join("?" * len(node_ids))
                        for e in conn.execute(
                            f"SELECT memory_id FROM graph_edges WHERE source_id IN ({qmarks}) OR target_id IN ({qmarks})",
                            node_ids * 2,
                        ).fetchall():
                            if e["memory_id"]:
                                mem_ids.add(e["memory_id"])
                    for r in conn.execute(
                        "SELECT id FROM documents WHERE status='active' AND (content LIKE ? OR key_facts LIKE ?) LIMIT 20",
                        ("%" + entity + "%", "%" + entity + "%"),
                    ).fetchall():
                        mem_ids.add(r["id"])
                if not mem_ids:
                    conn.close()
                    return self._send(200, {"entity": entity, "memories": []})
                ids = sorted(mem_ids, reverse=True)[:20]
                qmarks = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT id, content, type, domain, scope, importance FROM documents WHERE id IN ({qmarks})",
                    ids,
                ).fetchall()
                conn.close()
                return self._send(200, {"entity": entity, "memories": [dict(r) for r in rows]})
            if path.startswith("/v1/graph"):
                conn = get_conn()
                nodes = conn.execute(
                    "SELECT n.id, n.name, n.kind, n.created_at, "
                    "COALESCE(MAX(CASE WHEN d.status='active' THEN d.importance END), 0.5) AS importance, "
                    "COUNT(DISTINCT CASE WHEN d.status='active' THEN d.id END) AS memory_count, "
                    "COUNT(DISTINCT e.id) AS edge_count "
                    "FROM graph_nodes n "
                    "LEFT JOIN graph_edges e ON e.source_id=n.id OR e.target_id=n.id "
                    "LEFT JOIN documents d ON d.id=e.memory_id "
                    "GROUP BY n.id ORDER BY n.id DESC LIMIT 500"
                ).fetchall()
                edges = conn.execute(
                    "SELECT * FROM graph_edges ORDER BY id DESC LIMIT 500"
                ).fetchall()
                conn.close()
                return self._send(
                    200,
                    {"nodes": [dict(r) for r in nodes], "edges": [dict(r) for r in edges]},
                )
            if path.startswith("/v1/backups/list"):
                return self._send(200, {"backups": list_backups()})
            return self._send(404, {"error": "not found"})
        except OverflowError as e:
            return self._send(413, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": redact_text(str(e))[0]})

    def do_POST(self):
        try:
            if self._reject_browser_origin():
                return
            path = urlparse(self.path).path
            if path.startswith("/v1/v2/"):
                body = self._read_body()
                parts = [unquote(part) for part in path.strip("/").split("/")]
                store = self._v2_store()
                if path == "/v1/v2/memories":
                    if not body.get("content"):
                        return self._send(400, {"error": "content is required"})
                    created = add_memory(body)
                    memory_id = int(created["id"])
                    initial = {
                        key: body[key] for key in (
                            "memory_class", "storage_tier", "decision_status", "sensitivity_level",
                            "time_raw", "time_precision", "time_confidence", "time_inferred",
                            "event_time_start", "event_time_end", "source_ref", "source_message_id", "trace_id",
                        ) if key in body
                    }
                    if initial:
                        store.update_memory_lifecycle(memory_id, self._v2_changes(initial), 0,
                                                      actor=body.get("actor", "main_agent"),
                                                      reason=body.get("reason", "v2 memory created"))
                    return self._v2_call(lambda: {"memory": store.get_memory(memory_id)})
                if path == "/v1/v2/session-keys":
                    # POST /v1/v2/session-keys {workspace_id, session_id} → 创建（返回全 key 一次）
                    workspace_id = str(body.get("workspace_id") or "").strip()
                    session_id = str(body.get("session_id") or "").strip()
                    if not workspace_id or not session_id:
                        return self._send(400, {"error": "workspace_id and session_id are required"})
                    created = store.get_session_key(workspace_id, session_id)
                    return self._v2_call(lambda: {"key": created})
                if len(parts) >= 5 and parts[2] == "session-keys" and parts[4] == "rotate":
                    # POST /v1/v2/session-keys/<session_id>/rotate
                    session_id = unquote(parts[3])
                    workspace_id = str(body.get("workspace_id") or "").strip()
                    if not workspace_id:
                        return self._send(400, {"error": "workspace_id is required"})
                    rotated = store.rotate_session_key(workspace_id, session_id)
                    return self._v2_call(lambda: {"key": rotated})
                if len(parts) >= 6 and parts[2] == "sessions" and parts[4] == "memories" and parts[5] == "import":
                    # POST /v1/v2/sessions/<session_id>/memories/import
                    # body: {key?, payload, mode?: merge|replace, target_session_id?}
                    session_id = unquote(parts[3])
                    target = str(body.get("target_session_id") or session_id).strip()
                    mode = str(body.get("mode") or "merge").strip()
                    if mode not in ("merge", "replace"):
                        return self._send(400, {"error": "mode must be merge or replace"})
                    result = store.import_session_memories(target, body.get("payload") or {}, mode=mode)
                    return self._v2_call(lambda: result)
                if len(parts) >= 5 and parts[2] == "sessions" and parts[4] == "purge":
                    # POST /v1/v2/sessions/<session_id>/purge {hard?}
                    session_id = unquote(parts[3])
                    hard = bool(body.get("hard"))
                    return self._v2_call(lambda: store.purge_session(session_id, hard))
                if path == "/v1/v2/tasks":
                    if not body.get("title"):
                        return self._send(400, {"error": "title is required"})
                    return self._v2_call(lambda: {"task": store.create_task(
                        body["title"], status=body.get("status", "planned"),
                        parent_task_id=body.get("parent_task_id"), **{
                            key: body[key] for key in (
                                "description", "task_color", "blocked", "block_reason", "missing_conditions",
                                "completion_criteria", "source_message_id", "trace_id", "workspace_id", "session_id",
                                "max_active_tasks",
                            ) if key in body
                        })})
                if len(parts) >= 5 and parts[2] == "tasks":
                    task_id = parts[3]
                    if parts[4] == "delete":
                        return self._v2_call(lambda: {"task": store.delete_task(
                            task_id, actor=body.get("actor", "user"),
                            reason=body.get("reason", "deleted by user"),
                        )})
                    if parts[4] == "transition":
                        return self._v2_call(lambda: {"task": store.transition_task(
                            task_id, body["to_status"], int(body["expected_version"]),
                            reason=body.get("reason", ""), evidence=body.get("evidence", ""),
                            actor=body.get("actor", "main_agent"),
                            source_message_id=body.get("source_message_id", ""), trace_id=body.get("trace_id", ""),
                        )})
                    if parts[4] == "blocked":
                        return self._v2_call(lambda: {"task": store.set_task_blocked(
                            task_id, bool(body.get("blocked")), int(body["expected_version"]),
                            reason=body.get("reason", ""), missing_conditions=body.get("missing_conditions", []),
                            actor=body.get("actor", "main_agent"),
                        )})
                    if parts[4] == "color":
                        return self._v2_call(lambda: {"task": store.set_task_color(
                            task_id, body.get("task_color", "neutral"), int(body["expected_version"]),
                        )})
                    if parts[4] == "binding":
                        return self._v2_call(lambda: {"task": store.rebind_task(
                            task_id, body.get("workspace_id"), body.get("session_id"),
                            int(body["expected_version"]),
                        )})
                if len(parts) >= 5 and parts[2] == "cards" and parts[5:] == ["restore"]:
                    return self._v2_call(lambda: {"card": store.restore_state_card(
                        parts[4], parts[3], body["revision_id"], int(body["expected_version"]),
                        actor=body.get("actor", "main_agent"), reason=body.get("reason", ""),
                        source_message_id=body.get("source_message_id", ""),
                        tool_trace_id=body.get("tool_trace_id", ""), subagent_trace_id=body.get("subagent_trace_id", ""),
                    )})
                if path == "/v1/v2/recall":
                    return self._v2_call(lambda: store.recall(
                        body.get("query", ""), expand_to=body.get("expand_to", "active"),
                        limit=body.get("limit", 10), include_disputed=body.get("include_disputed", True),
                    ))
                if len(parts) >= 5 and parts[2] == "memories":
                    memory_id = int(parts[3])
                    if parts[4] == "lifecycle":
                        return self._v2_call(lambda: {"memory": store.update_memory_lifecycle(
                            memory_id, self._v2_changes(body.get("changes", {})), int(body["expected_version"]),
                            actor=body.get("actor", "model"), reason=body.get("reason", ""),
                            source_message_id=body.get("source_message_id", ""), trace_id=body.get("trace_id", ""),
                            explicit_execution=bool(body.get("explicit_execution")),
                        )})
                    if parts[4] == "migrate":
                        return self._v2_call(lambda: {"memory": store.migrate_memory(
                            memory_id, body["target_tier"], int(body["expected_version"]),
                            actor=body.get("actor", "main_agent"), reason=body.get("reason", ""),
                        )})
                    if parts[4] == "restore":
                        return self._v2_call(lambda: {"memory": store.restore_memory(
                            memory_id, int(body["expected_version"]), actor=body.get("actor", "main_agent"),
                            reason=body.get("reason", ""),
                        )})
                    if parts[4] == "dispute":
                        if "action" not in body:
                            return self._v2_call(lambda: {"revision_version": store.mark_disputed(
                                memory_id, int(body["expected_version"]), actor=body.get("actor", "main_agent"),
                                reason=body.get("reason", ""),
                            )})
                        return self._v2_call(lambda: {"revision_version": store.resolve_dispute(
                            memory_id, body["action"], int(body["expected_version"]), actor=body.get("actor", "user"),
                            changes=body.get("changes"), replacement_id=body.get("replacement_id"),
                            reason=body.get("reason", ""),
                        )})
                return self._send(404, {"error": "not found"})
            if path in ("/v1/sensitive/approvals", "/v1/sensitive/approve", "/v1/sensitive/authorize"):
                body = self._read_body()
                try:
                    return self._send(200, grant_sensitive_approval(
                        body.get("user_id"), body.get("session_id"), body.get("confirmed") is True
                    ))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
            if path in ("/v1/sensitive/sessions/end", "/v1/sensitive/session/end"):
                body = self._read_body()
                return self._send(200, {"revoked": end_sensitive_session(body.get("session_id"))})
            if path == "/v1/sensitive/expand" or path.startswith("/v1/sensitive/sources/") and path.endswith("/expand"):
                body = self._read_body()
                source_id = body.get("source_id")
                if source_id is None:
                    source_id = path.split("/")[-2]
                try:
                    result = expand_sensitive_source(source_id, body.get("approval_token"), body.get("user_id"), body.get("session_id"))
                    return self._send(200, {"source": result})
                except LookupError as exc:
                    return self._send(404, {"error": str(exc)})
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})
            if path == "/v1/backups/create":
                return self._send(200, create_backup())
            if path == "/v1/backups/restore":
                body = self._read_body()
                name = body.get("name") or ""
                if not name:
                    return self._send(400, {"error": "name required"})
                if restore_backup(name):
                    return self._send(200, {"restored": name, "documents": count_active()})
                return self._send(404, {"error": "backup not found"})
            if path == "/v1/embeddings":
                body = self._read_body()
                texts = body.get("input") or []
                if isinstance(texts, str):
                    texts = [texts]
                vecs = embed_texts(texts)
                return self._send(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"object": "embedding", "index": i, "embedding": v.tolist()}
                            for i, v in enumerate(vecs)
                        ],
                        "model": "bge-small-zh-v1.5",
                    },
                )
            if path == "/v1/maintenance/decay":
                body = self._read_body()
                return self._send(
                    200, run_decay(float(body.get("decay_rate", 0.01) or 0.01), bool(body.get("force")))
                )
            if path == "/v1/maintenance/consolidate":
                body = self._read_body()
                return self._send(
                    200,
                    consolidate_memories(
                        similarity=body.get("similarity"),
                        limit_groups=int(body.get("limit_groups", 0) or 0) or None,
                        dry_run=bool(body.get("dry_run")),
                    ),
                )
            if path == "/v1/maintenance/consolidate/candidates":
                body = self._read_body()
                return self._send(200, consolidate_memories(
                    similarity=body.get("similarity"),
                    limit_groups=int(body.get("limit_groups", 0) or 0) or None,
                    dry_run=True,
                ))
            if path == "/v1/maintenance/consolidate/apply":
                body = self._read_body()
                return self._send(200, apply_consolidation(body.get("groups")))
            if path == "/v1/maintenance/rebuild":
                body = self._read_body()
                return self._send(200, rebuild_indexes())
            if path == "/v1/maintenance/rule-candidates":
                body = self._read_body()
                return self._send(200, rule_candidates(
                    similarity=float(body.get("similarity") or 0.86),
                    min_occur=int(body.get("min_occur") or 2),
                ))
            if path == "/v1/maintenance/crystallize":
                body = self._read_body()
                res = crystallize_rules(body.get("ids") or [])
                rebuild_bm25()
                return self._send(200, res)
            if path == "/v1/memories/archive":
                body = self._read_body()
                return self._send(200, archive_memories(body.get("ids") or []))
            if path == "/v1/memories/restore":
                body = self._read_body()
                ok = restore_memory(body.get("id"))
                return self._send(200 if ok else 404, {"restored": ok})
            if path == "/v1/memories/archive-library":
                body = self._read_body()
                return self._v2_call(lambda: self._v2_store().archive_library(
                    str(body.get("library") or ""), topic=body.get("topic"),
                    actor=body.get("actor", "main_agent"), reason=body.get("reason", ""),
                ))
            if path == "/v1/memories/restore-tier":
                body = self._read_body()
                memory_id = int(body.get("id") or 0)
                store = self._v2_store()
                return self._v2_call(lambda: {"memory": store.migrate_memory(
                    memory_id, "active", int(body.get("expected_version", 0)),
                    actor=body.get("actor", "main_agent"), reason=body.get("reason", "restore from archive tier"),
                )})
            if path.startswith("/v1/info/") and len(path.strip("/").split("/")) == 4:
                # POST /v1/info/<domain>/<key>  {value: ...} —— 按需建库/覆盖写
                iparts = [unquote(x) for x in path.strip("/").split("/")]
                domain, key = iparts[2], iparts[3]
                body = self._read_body()
                if get_info_store().set(domain, key, body.get("value")) is None:
                    return self._send(404, {"error": "domain not allowed（白名单: env/project/status/config/ticket）"})
                return self._send(200, {"saved": domain + "/" + key})
            if path == "/v1/memories/injection-log":
                body = self._read_body()
                with _injection_lock:
                    if _last_injection is None: _last_injection = {}
                    _last_injection["full"] = str(body.get("full") or "")[:6000]
                return self._send(200, {"saved": True})
            if path == "/v1/topic-summaries/auto":
                body = self._read_body()
                topic_id = str(body.get("topic_id") or "").strip()
                text = str(body.get("text") or "").strip()
                if not topic_id or not text:
                    return self._send(400, {"error": "topic_id and text are required"})
                res = llm_chat(
                    "把以下对话历史压缩为一条可增值的摘要（保留：目标/决定/待办/关键事实，中文，≤300字）：\n"
                    + text[:8000],
                    system="你是记忆摘要助手：输出仅摘要。")
                if res.get("error"):
                    return self._send(500, {"error": res["error"]})
                summary = (res.get("content") or "").strip()
                conn = get_conn()
                row = conn.execute("SELECT COALESCE(MAX(seq),0) FROM topic_summaries WHERE topic_id=?", (topic_id,)).fetchone()
                seq = int(row[0]) + 1
                now = time.time()
                conn.execute(
                    "INSERT INTO topic_summaries (topic_id, seq, summary, start_time, end_time, prev_seq, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (topic_id, seq, summary, float(body.get("start_time") or now), float(body.get("end_time") or now), seq - 1 if seq > 1 else None, now),
                )
                conn.commit()
                conn.close()
                return self._send(200, {"topic_id": topic_id, "seq": seq, "summary": summary})
            if path == "/v1/topic-summaries":
                body = self._read_body()
                topic_id = str(body.get("topic_id") or "").strip()
                summary = str(body.get("summary") or "").strip()
                if not topic_id or not summary:
                    return self._send(400, {"error": "topic_id and summary are required"})
                conn = get_conn()
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM topic_summaries WHERE topic_id=?", (topic_id,)
                ).fetchone()
                seq = int(row[0]) + 1
                now = time.time()
                prev = seq - 1 if seq > 1 else None
                conn.execute(
                    "INSERT INTO topic_summaries (topic_id, seq, summary, start_time, end_time, prev_seq, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (topic_id, seq, summary, float(body.get("start_time") or now), float(body.get("end_time") or now), prev, now),
                )
                conn.commit()
                conn.close()
                return self._send(200, {"topic_id": topic_id, "seq": seq, "prev_seq": prev})
            if path == "/v1/memories/doc-link":
                body = self._read_body()
                return self._v2_call(lambda: self._v2_store().link_document(
                    int(body.get("memory_id") or 0), str(body.get("doc_path") or ""),
                    doc_kind=str(body.get("doc_kind") or "note"),
                    doc_version=str(body.get("doc_version") or ""),
                    relation=str(body.get("relation") or "derived_from"),
                    workspace_id=str(body.get("workspace_id") or ""),
                ))
            if path == "/v1/memories/add":
                body = self._read_body()
                r = add_memory(body)
                _process_attachments(r.get("id"), body)
                return self._send(200, r)
            if path == "/v1/memories/add_batch":
                body = self._read_body()
                items = body.get("items") or []
                return self._send(200, add_batch(items))
            if path == "/v1/config":
                body = self._read_body()
                return self._send(200, set_config_values(body))
            if path == "/v1/config/session/set":
                body = self._read_body()
                session_id = body.get("session_id")
                key = body.get("key")
                if not session_id or not key:
                    return self._send(400, {"error": "session_id and key required"})
                return self._send(200, set_session_config(session_id, key, body.get("value")))
            if path == "/v1/config/session/reset":
                body = self._read_body()
                session_id = body.get("session_id")
                key = body.get("key")
                if not session_id or not key:
                    return self._send(400, {"error": "session_id and key required"})
                return self._send(200, reset_session_config_key(session_id, key))
            if path == "/v1/settings/set":
                body = self._read_body()
                key = body.get("key") or ""
                if not key:
                    return self._send(400, {"error": "key required"})
                return self._send(200, set_setting(key, body.get("value")))
            if path == "/v1/cards/upsert":
                body = self._read_body()
                wid = body.get("workspace_id") or ""
                if not wid:
                    return self._send(400, {"error": "workspace_id required"})
                return self._send(200, put_card(wid, body))
            if path == "/v1/memories/search":
                body = self._read_body()
                results = search_memories(
                    body.get("query", ""),
                    k=body.get("k", 5),
                    session_id=body.get("session_id"),
                    workspace_id=body.get("workspace_id"),
                    domain=body.get("domain"),
                    type_=body.get("type"),
                    persona_id=body.get("persona_id"),
                    library=body.get("library"),
                    include_archived=bool(body.get("include_archived")),
                )
                note_injection(body.get("query"), results)
                return self._send(200, {"query": body.get("query"), "count": len(results), "results": results})
            if path == "/v1/atoms/add":
                body = self._read_body()
                aid = add_atom(int(body.get("memory_id") or 0), body)
                return self._send(200, {"id": aid})
            return self._send(404, {"error": "not found"})
        except OverflowError as e:
            return self._send(413, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": redact_text(str(e))[0]})

    def do_PUT(self):
        try:
            if self._reject_browser_origin():
                return
            path = urlparse(self.path).path
            if path.startswith("/v1/v2/"):
                body = self._read_body()
                parts = [unquote(part) for part in path.strip("/").split("/")]
                if len(parts) == 5 and parts[2] == "cards":
                    return self._v2_call(lambda: {"card": self._v2_store().put_state_card(
                        parts[4], parts[3], body.get("payload", {}),
                        expected_version=(int(body["expected_version"]) if body.get("expected_version") is not None else None),
                        task_id=body.get("task_id"),
                        actor=body.get("actor", "main_agent"), reason=body.get("reason", ""),
                        source_message_id=body.get("source_message_id", ""), tool_trace_id=body.get("tool_trace_id", ""),
                        subagent_trace_id=body.get("subagent_trace_id", ""),
                    )})
                return self._send(404, {"error": "not found"})
            if path.startswith("/v1/memories/"):
                doc_id = int(path.rsplit("/", 1)[-1])
                body = self._read_body()
                ok = update_memory(doc_id, body)
                return self._send(200 if ok else 404, {"updated": ok, "id": doc_id})
            return self._send(404, {"error": "not found"})
        except OverflowError as e:
            return self._send(413, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": redact_text(str(e))[0]})

    def do_DELETE(self):
        try:
            if self._reject_browser_origin():
                return
            path = urlparse(self.path).path
            if path.startswith("/v1/backups/"):
                name = os.path.basename(path.rsplit("/", 1)[-1])
                src = os.path.join(BACKUP_DIR, name)
                if not name or not os.path.isdir(src):
                    return self._send(404, {"error": "backup not found"})
                shutil.rmtree(src)
                return self._send(200, {"deleted": name})
            if path.startswith("/v1/memories/"):
                doc_id = int(path.rsplit("/", 1)[-1])
                ok = delete_memory(doc_id)
                return self._send(200 if ok else 404, {"deleted": ok, "id": doc_id})
            return self._send(404, {"error": "not found"})
        except OverflowError as e:
            return self._send(413, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": redact_text(str(e))[0]})


def main():
    init_db()
    _ = get_index()
    rebuild_bm25()
    lifecycle_stop = threading.Event()
    threading.Thread(
        target=lifecycle_scheduler,
        args=(lifecycle_stop,),
        name="deepmemory-lifecycle",
        daemon=True,
    ).start()
    srv = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"memory-server listening on http://localhost:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
