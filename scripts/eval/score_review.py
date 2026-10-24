#!/usr/bin/env python3
"""合并多复核员的 labels，计算指标与一致性。"""
import argparse, json, statistics

def load(p):
    return {json.loads(l)["id"]: json.loads(l) for l in open(p, encoding="utf-8") if l.strip()}

def jacc(a, b):
    a, b = set(a or []), set(b or [])
    return len(a & b) / len(a | b) if (a | b) else 1.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--labels", action="append", required=True, help="primary=file")
    ap.add_argument("--overlap", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    cases = {json.loads(l)["id"]: json.loads(l) for l in open(a.cases, encoding="utf-8") if l.strip()}
    labels = {}
    for spec in a.labels:
        name, path = spec.split("=", 1)
        labels[name] = load(path)
    primary = labels[sorted(labels)[0]]
    ids = [i for i in cases if i in primary]
    mem = [i for i in ids if cases[i]["kind"] == "memory" and cases[i].get("seed_id") is not None]
    task = [i for i in ids if cases[i]["kind"] == "task"]
    na = [i for i in ids if cases[i]["kind"] == "no_answer"]
    def stats(sub):
        returned = sum(len(cases[i]["results"]) for i in sub)
        useful = sum(len(primary[i].get("useful_ids") or []) for i in sub)
        prec = useful / returned if returned else 0
        return dict(n=len(sub), returned=returned, useful=useful, precision=round(prec, 3))
    res = {
        "cases": len(ids),
        "memory": stats(mem), "task": stats(task),
        "seed_hit": round(sum(1 for i in mem if primary[i].get("seed_hit")) / max(1, len(mem)), 3),
        "task_covered": round(sum(1 for i in task if primary[i].get("useful_ids")) / max(1, len(task)), 3),
        "count_ok": round(sum(1 for i in ids if primary[i].get("count_judgment") == "ok") / max(1, len(ids)), 3),
        "na_pass": round(sum(1 for i in na if primary[i].get("count_judgment") == "ok") / max(1, len(na)), 3),
    }
    if a.overlap and len(labels) > 1:
        other = labels[sorted(labels)[1]]
        ov = [i for i in json.load(open(a.overlap)) if i in primary and i in other]
        seeds = [i for i in ov if cases[i]["kind"] == "memory" and cases[i].get("seed_id") is not None]
        res["agreement"] = {
            "cases": len(ov),
            "useful_jaccard": round(statistics.mean([jacc(primary[i].get("useful_ids"), other[i].get("useful_ids")) for i in ov]), 3),
            "seed_hit_agree": round(sum(1 for i in seeds if bool(primary[i].get("seed_hit")) == bool(other[i].get("seed_hit"))) / max(1, len(seeds)), 3),
            "count_agree": round(sum(1 for i in ov if primary[i].get("count_judgment") == other[i].get("count_judgment")) / max(1, len(ov)), 3),
        }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.out:
        json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
