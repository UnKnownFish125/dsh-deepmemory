#!/usr/bin/env python3
"""从真实记忆库生成「召回评测集」初稿（只读 DB）。

用法：
  python3 build_recall_cases.py --db /path/memory.db --out cases.jsonl --n 30
每个 case：
  {"id","query","expected_ids","expected_count_range":[lo,hi],"facets":[...],"kind":"hit|no_answer","note"}
- hit：query 由一条真实记忆改写，expected_ids 为该记忆（+同主题兄弟条），facets 为必须覆盖的关键词。
- no_answer：库里不存在的主题，期望命中 <=1 条（测过召回/噪召）。
"""
import argparse, json, random, re, sqlite3, sys

HIT_TEMPLATES = {
    "fact": ["关于「{kw}」之前记录过什么事实？", "「{kw}」这条信息具体是什么？"],
    "decision": ["之前关于「{kw}」的决定/结论是什么？", "我们当时为什么选「{kw}」？"],
    "preference": ["用户对「{kw}」的偏好是什么？", "关于「{kw}」，用户要求怎么做？"],
    "rule": ["有没有关于「{kw}」的硬性规则？", "「{kw}」这条约束具体内容？"],
    "plan": ["「{kw}」的计划/下一步是什么？", "之前定的「{kw}」怎么做？"],
    "episode": ["「{kw}」这件事后来怎么了？", "记录里「{kw}」发生了什么？"],
}
NO_ANSWER = [
    ("火星基地的氧气配额是多少？", ["火星基地", "氧气配额"]),
    ("蓝鲸项目的服务器密码轮换周期？", ["蓝鲸项目", "轮换"]),
    ("上周三的客户会议纪要结论是什么？", ["客户会议", "纪要"]),
    ("南极科考站柴油发电机型号？", ["南极", "发电机"]),
    ("月球背面中继卫星的发射窗口？", ["月球", "中继卫星"]),
]

import jieba, jieba.posseg as pseg
STOP = set("的了是我你他她它们这那有在和不就都而及与或者之其所以因为但是如果然后之前以后以及一个这个那个".split()) | {"我们","你们","他们","什么","怎么","如何","可以","需要","应该","已经","还是","就是","没有","不是","时候","问题","东西","事情","地方","记录","内容"}

def keywords(text):
    """用 jieba 词性挑名词/英文词，避免切出无意义的中文串。"""
    nouns, others, seen = [], [], set()
    for tok, flag in pseg.cut(text or ""):
        tok = tok.strip()
        if not tok or tok in STOP or tok in seen or len(tok) < 2:
            continue
        if flag.startswith("n") or re.match(r"^[A-Za-z][A-Za-z0-9_.\-]{3,}$", tok):
            seen.add(tok); nouns.append(tok)
        elif flag.startswith(("v", "a")):
            seen.add(tok); others.append(tok)
    return (nouns + others)[:6]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--workspace", default="")
    a = ap.parse_args()
    random.seed(a.seed)
    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    c.row_factory = sqlite3.Row
    where = "status='active' AND length(content) BETWEEN 30 AND 220 AND has_sensitive=0 AND scope IN ('workspace','global') AND workspace_id<>''"
    args = []
    if a.workspace:
        where += " AND workspace_id=?"; args.append(a.workspace)
    rows = c.execute("SELECT id, content, key_facts, type, scope, workspace_id FROM documents WHERE %s" % where, args).fetchall()
    pool = []
    for r in rows:
        kws = keywords(r["content"])
        if len(kws) < 2: continue
        pool.append(r)
    random.shuffle(pool)
    pool = pool[: max(a.n * 2, a.n)]
    cases, used = [], set()
    for r in pool:
        if len(cases) >= a.n: break
        kws = keywords(r["content"])
        kw = None
        for cand in kws[:4]:
            df = c.execute("SELECT COUNT(*) FROM documents WHERE status='active' AND content LIKE ?", ("%"+cand+"%",)).fetchone()[0]
            if 2 <= df <= 300:
                kw = cand; break
        if kw is None:
            continue
        typ = (r["type"] or "fact").strip() or "fact"
        tmpl = (HIT_TEMPLATES.get(typ) or HIT_TEMPLATES["fact"])[0]
        query = tmpl.format(kw=kw)
        # 兄弟条：同 workspace + 内容含同一关键词（最多 2 条），保证“相关集”定义可解释
        sib = c.execute(
            "SELECT id FROM documents WHERE status='active' AND workspace_id=? AND id<>? AND content LIKE ? AND length(content) BETWEEN 20 AND 220 LIMIT 2",
            (r["workspace_id"], r["id"], "%" + kw + "%"),
        ).fetchall()
        expected = [r["id"]] + [x["id"] for x in sib]
        facets = [kw] + [k for k in kws if k != kw][:2]
        cases.append({
            "id": "hit-%03d" % (len(cases) + 1),
            "kind": "hit",
            "query": query,
            "expected_ids": expected,
            "expected_count_range": [1, max(2, len(expected) + 2)],
            "facets": facets,
            "workspace_id": r["workspace_id"],
            "note": "seed memory %s type=%s scope=%s" % (r["id"], typ, r["scope"]),
        })
        used.add(r["id"])
    for i, (q, facets) in enumerate(NO_ANSWER):
        cases.append({
            "id": "na-%03d" % (i + 1),
            "kind": "no_answer",
            "query": q,
            "expected_ids": [],
            "expected_count_range": [0, 1],
            "facets": [],
            "note": "库中不应存在该主题",
        })
    with open(a.out, "w", encoding="utf-8") as f:
        for x in cases:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print("cases:", len(cases), "->", a.out)

if __name__ == "__main__":
    main()
