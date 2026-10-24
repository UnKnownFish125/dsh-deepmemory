#!/usr/bin/env python3
"""记忆召回评测器：用真实 HTTP 搜索接口跑测试集，产出三项核心指标。

指标（对应需求）：
  1) 召回是否准确       -> hit@k、Recall@k、MRR、noise_rate
  2) 数量是否合理       -> count_ok_rate（结果条数是否落在 expected_count_range）
  3) 记忆是否补全       -> facet_coverage（必须覆盖的关键词/事实片段命中比例）

用法：
  python3 run_recall_eval.py --server http://127.0.0.1:6240 \
      --token-file /www/dsh-test-home/.dsh-memory-api-token \
      --cases scripts/eval/recall_cases.jsonl --k 10 \
      --workspace deepseek-harness --out-md /tmp/recall-report.md
"""
import argparse, json, statistics, urllib.request

def post(url, token, payload, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Authorization": "Bearer " + token, "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:6240")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--cases", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--workspace", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--out-json", default="")
    a = ap.parse_args()
    token = open(a.token_file, encoding="utf-8").read().strip()
    cases = [json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()]

    rows = []
    for c in cases:
        payload = {"query": c["query"], "k": a.k}
        ws = c.get("workspace_id") or a.workspace
        if ws: payload["workspace_id"] = ws
        if a.session: payload["session_id"] = a.session
        try:
            res = post(a.server.rstrip("/") + "/v1/memories/search", token, payload)
        except Exception as e:
            rows.append({**c, "error": str(e), "ids": [], "recall": 0.0, "rr": 0.0,
                         "count_ok": False, "facet_cov": None, "noise": 0.0, "n": 0})
            continue
        items = res.get("results") or []
        ids = [int(x.get("id")) for x in items if x.get("id") is not None]
        text = "\n".join(str(x.get("content") or "") for x in items)
        exp = set(int(x) for x in c.get("expected_ids") or [])
        lo, hi = c.get("expected_count_range") or [0, 999]
        hits = exp & set(ids)
        recall = (len(hits) / len(exp)) if exp else 0.0
        rr = 0.0
        for i, mid in enumerate(ids, 1):
            if mid in exp: rr = 1.0 / i; break
        count_ok = lo <= len(ids) <= hi
        facets = c.get("facets") or []
        facet_cov = (sum(1 for f in facets if f and f in text) / len(facets)) if facets else None
        noise = (len(set(ids) - exp) / len(ids)) if ids and exp else 0.0
        rows.append({**c, "ids": ids, "hits": sorted(hits), "recall": recall, "rr": rr,
                     "count_ok": count_ok, "facet_cov": facet_cov, "noise": noise, "n": len(ids)})

    hit_rows = [r for r in rows if r["kind"] == "hit"]
    na_rows = [r for r in rows if r["kind"] != "hit"]
    hit_rate = sum(1 for r in hit_rows if r["hits"]) / max(1, len(hit_rows))
    m_recall = statistics.mean([r["recall"] for r in hit_rows]) if hit_rows else 0.0
    m_mrr = statistics.mean([r["rr"] for r in hit_rows]) if hit_rows else 0.0
    count_ok = sum(1 for r in rows if r["count_ok"]) / max(1, len(rows))
    covs = [r["facet_cov"] for r in hit_rows if r["facet_cov"] is not None]
    m_cov = statistics.mean(covs) if covs else 0.0
    m_noise = statistics.mean([r["noise"] for r in hit_rows]) if hit_rows else 0.0
    avg_n = statistics.mean([r["n"] for r in rows]) if rows else 0.0
    na_pass = sum(1 for r in na_rows if r["count_ok"]) / max(1, len(na_rows))

    summary = {
        "cases": len(rows), "k": a.k, "workspace": a.workspace,
        "hit_rate@k": round(hit_rate, 4), "recall@k": round(m_recall, 4), "mrr": round(m_mrr, 4),
        "count_ok_rate": round(count_ok, 4), "facet_coverage": round(m_cov, 4),
        "noise_rate": round(m_noise, 4), "avg_results": round(avg_n, 2),
        "no_answer_pass_rate": round(na_pass, 4),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if a.out_md:
        with open(a.out_md, "w", encoding="utf-8") as f:
            f.write("# 记忆召回评测报告\n\n")
            f.write("## 汇总\n\n| 指标 | 值 | 含义 |\n|---|---|---|\n")
            f.write(f"| hit_rate@{a.k} | {hit_rate:.3f} | 期望记忆出现在前 {a.k} 条的比例 |\n")
            f.write(f"| Recall@{a.k} | {m_recall:.3f} | 期望记忆被找回的比例 |\n")
            f.write(f"| MRR | {m_mrr:.3f} | 首个命中排名的倒数均值 |\n")
            f.write(f"| count_ok_rate | {count_ok:.3f} | 召回条数落在期望区间的比例 |\n")
            f.write(f"| facet_coverage | {m_cov:.3f} | 必需事实片段覆盖比例（补全度） |\n")
            f.write(f"| noise_rate | {m_noise:.3f} | 召回中非期望条目的比例 |\n")
            f.write(f"| avg_results | {avg_n:.1f} | 平均召回条数 |\n")
            f.write(f"| no_answer_pass | {na_pass:.3f} | 无答案问题不滥召回的比例 |\n\n")
            f.write("## 逐条\n\n| case | kind | query | 召回数 | 命中 | recall | facet | 条数OK |\n|---|---|---|---|---|---|---|---|\n")
            for r in rows:
                f.write("| %s | %s | %s | %d | %s | %.2f | %s | %s |\n" % (
                    r["id"], r["kind"], r["query"].replace("|", "/"), r["n"],
                    ",".join(map(str, r.get("hits") or [])) or "-", r["recall"],
                    "-" if r["facet_cov"] is None else "%.2f" % r["facet_cov"],
                    "Y" if r["count_ok"] else "N"))
    if a.out_json:
        json.dump({"summary": summary, "rows": rows}, open(a.out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
