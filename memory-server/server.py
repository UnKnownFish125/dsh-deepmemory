#!/opt/AstrBot/venv/bin/python3
# -*- coding: utf-8 -*-
"""
DSH memory-server — semantic long-term memory backend for the DeepSeek Harness.

Aligned with AstrBot livingmemory's storage/retrieval strategy:
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import faiss
import numpy as np

from v2_domain import V2_SCHEMA_VERSION, install_v2_schema

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODEL_DIR = os.path.join(BASE_DIR, "models")
DB_PATH = os.path.join(DATA_DIR, "memory.db")
INDEX_PATH = os.path.join(DATA_DIR, "memory.faiss")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
PORT = int(os.environ.get("MEMORY_SERVER_PORT", "6230"))
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


def get_model():
    global _embed_model
    with _embed_lock:
        if _embed_model is None:
            from fastembed import TextEmbedding

            _embed_model = TextEmbedding(
                model_name="BAAI/bge-small-zh-v1.5", cache_dir=MODEL_DIR
            )
            list(_embed_model.embed(["warmup"]))
        return _embed_model


def embed_texts(texts):
    texts = [str(t or "") for t in texts]
    model = get_model()
    return [np.asarray(v, dtype=np.float32) for v in model.embed(texts)]


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
  keywords TEXT DEFAULT ''
);
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
  expires_at REAL DEFAULT 0
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
  created_at REAL
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
                conn.execute(stmt)
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


# ---------------------------------------------------------------- faiss

_index_lock = threading.Lock()
_index = None
# 检索缓存：{(query,k,session,workspace,domain,type,persona): (ts, results)}
_search_cache = {}


def get_index():
    global _index
    with _index_lock:
        if _index is None:
            if os.path.exists(INDEX_PATH):
                _index = faiss.read_index(INDEX_PATH)
            else:
                _index = faiss.IndexIDMap(faiss.IndexFlatL2(DIM))
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
        self.docs = {}
        self.df = {}
        self.avgdl = 1.0

    def add(self, doc_id, text):
        tokens = tokenize(text)
        if not tokens:
            return
        self.docs[doc_id] = tokens
        seen = set()
        for t in tokens:
            if t not in seen:
                self.df[t] = self.df.get(t, 0) + 1
                seen.add(t)
        total = sum(len(v) for v in self.docs.values())
        self.avgdl = total / max(1, len(self.docs))

    def remove(self, doc_id):
        self.docs.pop(doc_id, None)
        total = sum(len(v) for v in self.docs.values())
        self.avgdl = total / max(1, len(self.docs))

    def search(self, query, k):
        tokens = tokenize(query)
        if not tokens:
            return []
        n = max(1, len(self.docs))
        scores = {}
        for t in tokens:
            if t not in self.df:
                continue
            idf = math.log(1.0 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for doc_id, doc_tokens in self.docs.items():
                tf = doc_tokens.count(t)
                if tf == 0:
                    continue
                dl = len(doc_tokens)
                denom = tf + 1.5 * (1 - 0.75 + 0.75 * dl / max(1.0, self.avgdl))
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (tf * 1.5) / denom
        return sorted(scores.items(), key=lambda kv: -kv[1])[:k]


_bm25 = BM25()


def get_bm25():
    return _bm25


def rebuild_bm25():
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, key_facts, keywords FROM documents WHERE status='active'"
    ).fetchall()
    conn.close()
    bm = get_bm25()
    bm.docs = {}
    bm.df = {}
    for r in rows:
        text = " ".join(x for x in (r["content"], r["key_facts"], r["keywords"]) if x)
        bm.add(r["id"], text)


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


def graph_search(query, k):
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
            "SELECT id FROM documents WHERE status='active' AND (content LIKE ? OR key_facts LIKE ?) LIMIT 8",
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
    return default if v is None else bool(v)


def rrf_fuse(ranked_lists):
    rrf_k = int(cfg_float("fusion_strategy.rrf_k", RRF_K))
    fused = {}
    for ranked in ranked_lists:
        for rank, (doc_id, _score) in enumerate(ranked):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)
    return sorted(fused.items(), key=lambda kv: -kv[1])


def apply_weighting(fused, now=None):
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
        f"SELECT * FROM documents WHERE id IN ({qmarks}) AND status='active'", ids
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
        item = dict(r)
        item["rrf_score"] = round(rrf, 4)
        item["final_score"] = round(final, 4)
        item["recency_weight"] = round(recency, 4)
        item["days_old"] = round(days_old, 2)
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
):
    if not query or not query.strip():
        return []
    k = max(1, min(int(k), 50))
    # 检索缓存：相同查询参数组合在 TTL 内直接复用（livingmemory 检索缓存）
    cache_enabled = cfg_bool("search_cache.enabled", True)
    cache_key = (query.strip()[:200], k, session_id or "", workspace_id or "", domain or "", type_ or "", persona_id or "")
    if cache_enabled:
        hit = _search_cache.get(cache_key)
        if hit and time.time() - hit[0] < cfg_float("search_cache.ttl_seconds", 45.0):
            return hit[1]
    vecs = embed_texts([query])
    vres = vector_search(vecs[0], k * 3)
    bres = get_bm25().search(query, k * 3)
    gres = graph_search(query, k * 3)
    fused = rrf_fuse([vres, bres, gres])
    weighted = apply_weighting(fused)
    out = []
    for r in weighted:
        if not scope_allows(r, session_id, workspace_id):
            continue
        if domain and r["domain"] != domain:
            continue
        if type_ and r["type"] != type_:
            continue
        if persona_id and r["persona_id"] and r["persona_id"] != persona_id:
            continue
        out.append(r)
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
        # 访问强化：被召回的活跃原子刷新强化时间并延长 TTL（livingmemory access reinforcement）
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
    content = (payload.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    now = time.time()
    uuid_s = payload.get("uuid") or str(uuid.uuid4())
    importance = float(payload.get("importance", 0.5) or 0.5)
    key_facts = payload.get("key_facts") or ""
    persona_summary = payload.get("persona_summary") or ""
    canonical_summary = payload.get("canonical_summary") or ""
    # 检索文本 = 摘要 + 关键事实，提升检索信息密度（对齐 livingmemory）
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
                " last_access_at=?, access_count=access_count+1 WHERE id=?",
                (content, key_facts, persona_summary, canonical_summary, importance, now, top_id),
            )
            conn.commit()
            conn.close()
            _save_source(top_id, payload.get("source") or "")
            bm = get_bm25()
            bm.remove(top_id)
            bm.add(top_id, search_text + " " + (payload.get("keywords") or ""))
            return {"id": top_id, "uuid": uuid_s, "merged": True}
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO documents (uuid, content, key_facts, persona_summary,"
        " canonical_summary, type, domain, scope, workspace_id, session_id,"
        " persona_id, importance, created_at, last_access_at, access_count,"
        " status, keywords) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            uuid_s,
            content,
            key_facts,
            persona_summary,
            canonical_summary,
            payload.get("type") or "fact",
            payload.get("domain") or "work",
            payload.get("scope") or "session",
            payload.get("workspace_id") or "",
            payload.get("session_id") or "",
            payload.get("persona_id") or "",
            importance,
            now,
            now,
            0,
            "active",
            payload.get("keywords") or "",
        ),
    )
    doc_id = cur.lastrowid
    conn.commit()
    conn.close()
    _save_source(doc_id, payload.get("source") or "")
    idx = get_index()
    with _index_lock:
        idx.add_with_ids(vec.reshape(1, -1), np.asarray([doc_id], dtype=np.int64))
    save_index()
    get_bm25().add(doc_id, search_text + " " + (payload.get("keywords") or ""))
    return {"id": doc_id, "uuid": uuid_s, "merged": False}


def _save_source(memory_id, source_text):
    """原文保留：高价值记忆的来源消息全文存入 sources 表（审计/回溯用）。"""
    source_text = (source_text or "").strip()
    if not source_text:
        return
    conn = get_conn()
    conn.execute(
        "INSERT INTO sources (memory_id, content, created_at) VALUES (?,?,?)",
        (int(memory_id), source_text[:8000], time.time()),
    )
    conn.commit()
    conn.close()


def add_atom(memory_id, payload):
    now = time.time()
    ttl = float(payload.get("ttl_days", 30) or 30)
    content = (payload.get("content") or "").strip()
    # 原子级查重（livingmemory 对齐）：与同记忆的活跃原子 Jaccard 相似，
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
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO atoms (memory_id, atom_type, content, entities, importance,"
        " confidence, ttl_days, decay_type, status, created_at, last_accessed_at,"
        " reinforcement_count, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(memory_id),
            payload.get("atom_type") or "unknown",
            content,
            json.dumps(payload.get("entities") or [], ensure_ascii=False),
            float(payload.get("importance", 0.5) or 0.5),
            float(payload.get("confidence", 0.7) or 0.7),
            ttl,
            payload.get("decay_type") or "exponential",
            "active",
            now,
            now,
            0,
            now + ttl * 86400,
        ),
    )
    aid = cur.lastrowid
    conn.commit()
    conn.close()
    return aid


def add_entity(memory_id, payload):
    name = (payload.get("name") or "").strip() if isinstance(payload, dict) else ""
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
    return nid


def add_relation(memory_id, payload):
    """新增实体关系边：source/relation/target；节点不存在时自动创建。"""
    src = (payload.get("source") or "").strip() if isinstance(payload, dict) else ""
    dst = (payload.get("target") or "").strip() if isinstance(payload, dict) else ""
    rel = (payload.get("relation") or "").strip() if isinstance(payload, dict) else ""
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
    return cur.lastrowid


def get_sources(memory_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, created_at FROM sources WHERE memory_id=? ORDER BY id DESC",
        (int(memory_id),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    fields = {k: payload[k] for k in allowed if k in payload}
    if not fields:
        return False
    conn = get_conn()
    row = conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row is None:
        conn.close()
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [doc_id]
    conn.execute(f"UPDATE documents SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()
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
            results.append({"error": str(e), "content": str(item.get("content", ""))[:80]})
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


# ---------------------------------------------------------------- config center

CONFIG_SCHEMA_PATH = os.path.join(BASE_DIR, "config_schema.json")


def get_config_schema():
    try:
        with open(CONFIG_SCHEMA_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def get_config_values():
    conn = get_conn()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE 'deepmemory.%'"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        key = r["key"][len("deepmemory."):]
        try:
            out[key] = json.loads(r["value"])
        except Exception:
            out[key] = r["value"]
    return out


def set_config_values(payload):
    count = 0
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            continue
        set_setting("deepmemory." + key, value)
        count += 1
    return {"saved": count}


# ---------------------------------------------------------------- lifecycle

DECAY_PROTECTED = 0.85      # importance above this is exempt from decay
DECAY_CLEANUP_IMPORTANCE = 0.1
DECAY_CLEANUP_DAYS = 30.0
DECAY_LAST_KEY = "last_decay_at"


def count_active():
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE status='active'"
    ).fetchone()["c"]
    conn.close()
    return n


def run_decay(decay_rate=0.01, force=False):
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
    now = time.time()
    last = get_setting(DECAY_LAST_KEY)
    last = float(last) if last else 0.0
    if not force and now - last < 86400:
        return {
            "skipped": True,
            "next_run_in_seconds": int(86400 - (now - last)),
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
    return {"decayed": decayed, "archived": len(archived_ids), "documents": count_active()}


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


def rebuild_indexes():
    """索引重建：指纹校验 + 影子重建（临时文件生成后原子替换，livingmemory 对齐）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, content, key_facts FROM documents WHERE status='active'"
    ).fetchall()
    conn.close()
    doc_ids = [r["id"] for r in rows]
    fingerprint = {"count": len(doc_ids), "max_id": max(doc_ids) if doc_ids else 0}
    old_ntotal = get_index().ntotal
    texts = [str(r["content"]) + " " + str(r["key_facts"] or "") for r in rows]
    if texts:
        vecs = embed_texts(texts)
        mat = np.vstack([normalize(v) for v in vecs]).astype(np.float32)
        ids = np.asarray(doc_ids, dtype=np.int64)
        d = mat.shape[1]
        tmp = faiss.IndexIDMap(faiss.IndexFlatL2(d))
        tmp.add_with_ids(mat, ids)
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
        "index_after": get_index().ntotal,
    }


def consolidate_memories(similarity=None, limit_groups=None, dry_run=False):
    """记忆整合（livingmemory 对齐）：语义聚类高度相似的活跃记忆，组内保留
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
    for g in groups or []:
        pid = int(g.get("primary_id"))
        archived = [int(x) for x in (g.get("archived_ids") or [])]
        summary = str(g.get("canonical_summary") or "").strip()
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
            "UPDATE documents SET canonical_summary=? WHERE id=?",
            ((existing + ("\n" if existing else "") + summary)[:4000], pid),
        )
        for mid in archived:
            conn.execute("UPDATE documents SET status='archived' WHERE id=?", (mid,))
            get_bm25().remove(mid)
        done += len(archived)
    conn.commit()
    conn.close()
    return {"merged": done, "groups": len(groups or [])}


def get_overview(workspace_id=None, session_id=None):
    conn = get_conn()
    mems = [
        dict(r)
        for r in conn.execute(
            "SELECT id, content, type, domain, scope, importance, created_at,"
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
            payload.get("goal") or "",
            payload.get("current_plan") or "",
            json.dumps(payload.get("key_decisions") or [], ensure_ascii=False),
            json.dumps(payload.get("in_progress") or [], ensure_ascii=False),
            json.dumps(payload.get("next_steps") or [], ensure_ascii=False),
            version,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return {"workspace_id": workspace_id, "version": version}


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    server_version = "memory-server/0.1"

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)
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
            if path.startswith("/v1/settings/"):
                key = path.rsplit("/", 1)[-1]
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
            if path.startswith("/v1/memories/"):
                parts = path.strip("/").split("/")
                doc_id = int(parts[-1] if parts[-1] != "source" else parts[-2])
                if parts[-1] == "source":
                    return self._send(200, {"sources": get_sources(doc_id)})
                conn = get_conn()
                r = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
                conn.close()
                if r is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"memory": dict(r)})
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
                    "SELECT * FROM graph_nodes ORDER BY id DESC LIMIT 500"
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
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        try:
            path = urlparse(self.path).path
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
                return self._send(200, consolidate_memories(dry_run=True))
            if path == "/v1/maintenance/consolidate/apply":
                body = self._read_body()
                return self._send(200, apply_consolidation(body.get("groups")))
            if path == "/v1/maintenance/rebuild":
                body = self._read_body()
                return self._send(200, rebuild_indexes())
            if path == "/v1/memories/archive":
                body = self._read_body()
                return self._send(200, archive_memories(body.get("ids") or []))
            if path == "/v1/memories/restore":
                body = self._read_body()
                ok = restore_memory(body.get("id"))
                return self._send(200 if ok else 404, {"restored": ok})
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
                )
                return self._send(200, {"query": body.get("query"), "count": len(results), "results": results})
            if path == "/v1/atoms/add":
                body = self._read_body()
                now = time.time()
                conn = get_conn()
                cur = conn.execute(
                    "INSERT INTO atoms (memory_id, atom_type, content, entities,"
                    " importance, confidence, ttl_days, decay_type, status,"
                    " created_at, last_accessed_at, reinforcement_count, expires_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        int(body.get("memory_id") or 0),
                        body.get("atom_type") or "unknown",
                        body.get("content") or "",
                        json.dumps(body.get("entities") or [], ensure_ascii=False),
                        float(body.get("importance", 0.5) or 0.5),
                        float(body.get("confidence", 0.7) or 0.7),
                        float(body.get("ttl_days", 30) or 30),
                        body.get("decay_type") or "exponential",
                        "active",
                        now,
                        now,
                        0,
                        now + float(body.get("ttl_days", 30) or 30) * 86400,
                    ),
                )
                aid = cur.lastrowid
                conn.commit()
                conn.close()
                return self._send(200, {"id": aid})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_PUT(self):
        try:
            path = urlparse(self.path).path
            if path.startswith("/v1/memories/"):
                doc_id = int(path.rsplit("/", 1)[-1])
                body = self._read_body()
                ok = update_memory(doc_id, body)
                return self._send(200 if ok else 404, {"updated": ok, "id": doc_id})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_DELETE(self):
        try:
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
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    init_db()
    _ = get_index()
    rebuild_bm25()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"memory-server listening on http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
