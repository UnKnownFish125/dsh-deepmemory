#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb-query.py — deepmemory 知识库模型查询服务（生产 6230 / 测试机 6240）
知识库分库（bias/core/eco/project/runtime）+ 归档 + 文档溯源接口的主动调用封装。
用法：
  python3 kb-query.py search "关键词" [--library core] [--k 5] [--archived]
  python3 kb-query.py libraries [--library core]
  python3 kb-query.py for-doc "/绝对/路径/contract.md" [--workspace ws1]
  python3 kb-query.py doc-link <memory_id> <doc_path> [--kind contract] [--version v0.2] [--relation derived_from]
  python3 kb-query.py archive <library> [--topic 任务看板] [--reason "..."]
  python3 kb-query.py restore-tier <memory_id>
  python3 kb-query.py health
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse

SERVER = os.environ.get("MEMORY_SERVER_URL", "http://127.0.0.1:6230")
TOKEN_FILES = [
    os.environ.get("MEMORY_API_TOKEN_FILE", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "api-token"),
    os.path.expanduser("~/.dsh-memory-api-token"),
]


def read_token():
    for path in TOKEN_FILES:
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                token = fh.read().strip()
            if token:
                return token
        except OSError:
            continue
    return ""


def req(method, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = read_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(SERVER + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main():
    p = argparse.ArgumentParser(description="deepmemory 知识库查询服务")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="语义检索（支持 library 过滤/归档）")
    s.add_argument("query")
    s.add_argument("--library", default="")
    s.add_argument("--k", type=int, default=5)
    s.add_argument("--archived", action="store_true")
    s.add_argument("--workspace", default="")

    sub.add_parser("libraries", help="库目录（条目/归档/主题分布）")

    f = sub.add_parser("for-doc", help="文档→记忆反查")
    f.add_argument("path")
    f.add_argument("--workspace", default="")

    d = sub.add_parser("doc-link", help="记忆↔文档建链")
    d.add_argument("memory_id", type=int)
    d.add_argument("doc_path")
    d.add_argument("--kind", default="note")
    d.add_argument("--version", default="")
    d.add_argument("--relation", default="derived_from")
    d.add_argument("--workspace", default="")

    a = sub.add_parser("archive", help="库级归档（bias 拒绝）")
    a.add_argument("library")
    a.add_argument("--topic", default="")
    a.add_argument("--reason", default="", required=True)

    r = sub.add_parser("restore-tier", help="归档恢复（archive→active）")
    r.add_argument("memory_id", type=int)
    r.add_argument("--expected_version", type=int, default=0)

    sub.add_parser("health", help="服务健康")

    args = p.parse_args()

    if args.cmd == "health":
        code, body = req("GET", "/v1/health")
        print(f"[{code}]", json.dumps(body, ensure_ascii=False))
        return
    if args.cmd == "libraries":
        code, body = req("GET", "/v1/memories/libraries")
        print(f"[{code}]")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    if args.cmd == "search":
        payload = {"query": args.query, "k": args.k}
        if args.library:
            payload["library"] = args.library
        if args.archived:
            payload["include_archived"] = True
        if args.workspace:
            payload["workspace_id"] = args.workspace
        code, body = req("POST", "/v1/memories/search", payload)
        print(f"[{code}] count={body.get('count', 0)}")
        for r in body.get("results", []):
            print(f"  #{r['id']} [{r.get('library','?')}/{r.get('scope','?')}/i{r.get('importance',0):.2f}] {str(r.get('content',''))[:120]}")
        return
    if args.cmd == "for-doc":
        code, body = req("GET", "/v1/memories/for-doc?path=" + urllib.parse.quote(args.path, safe=""))
        print(f"[{code}]")
        for link in body.get("links", []):
            print(f"  memory#{link.get('memory_id')} {link.get('relation')} [{link.get('doc_kind')}/{link.get('doc_version')}]")
        return
    if args.cmd == "doc-link":
        code, body = req("POST", "/v1/memories/doc-link", {
            "memory_id": args.memory_id, "doc_path": args.doc_path,
            "doc_kind": args.kind, "doc_version": args.version,
            "relation": args.relation, "workspace_id": args.workspace,
        })
        print(f"[{code}]", json.dumps(body, ensure_ascii=False))
        return
    if args.cmd == "archive":
        code, body = req("POST", "/v1/memories/archive-library", {
            "library": args.library, "topic": args.topic or None, "reason": args.reason,
        })
        print(f"[{code}]", json.dumps(body, ensure_ascii=False))
        return
    if args.cmd == "restore-tier":
        code, body = req("POST", "/v1/memories/restore-tier", {
            "id": args.memory_id, "expected_version": args.expected_version, "reason": "kb-query restore",
        })
        print(f"[{code}]", json.dumps(body, ensure_ascii=False))
        return


if __name__ == "__main__":
    main()
