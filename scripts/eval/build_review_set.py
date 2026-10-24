#!/usr/bin/env python3
"""生成「人工复核版」评测集：记忆改写 query + 真实任务 prompt + 无答案题，
并直接跑一次搜索，把 top-k 结果落盘供 Agent/人复核（只读）。"""
import argparse, json, random, re, sqlite3, urllib.request
import jieba.posseg as pseg

STOP = set("的了是我你他她它们这那有在和不就都而及与或者之其所以因为但是如果然后之前以后以及一个这个那个".split()) | {"我们","你们","他们","什么","怎么","如何","可以","需要","应该","已经","还是","就是","没有","不是","时候","问题","东西","事情","地方","记录","内容","什么"}
TMPL = {
 "fact": ["关于「{kw}」，之前记录过什么？", "「{kw}」具体是什么情况？"],
 "decision": ["之前关于「{kw}」的决定是什么？", "为什么在「{kw}」上做了这个决定？"],
 "preference": ["用户对「{kw}」的偏好/要求是什么？", "「{kw}」上用户希望怎么做？"],
 "rule": ["有没有关于「{kw}」的硬规则？", "「{kw}」这条约束的具体内容？"],
 "plan": ["「{kw}」的计划/下一步是什么？", "之前定的「{kw}」怎么推进？"],
 "episode": ["「{kw}」这件事后来怎样了？", "记录里「{kw}」发生了什么？"],
}
TASKS = [
 "帮我给 deepmemory 的抽取加一个持久化 pending 队列",
 "生产重启 dsh-web 之前要检查哪些条件？",
 "memory_source 报 lossless JSON 错误怎么修？",
 "给 session-query 的 5 个工具加 workspace 过滤要动哪些文件？",
 "怎么把一条约束写进 literature bias 库？",
 "dsh-web 升级到 0.2.1 后 toolFilter 报错怎么处理？",
 "deepmemory 的 state_cards 表为什么要加 workspace_id？",
 "我现在的记忆注入块是怎么拼出来的？",
 "怎么在不重启的情况下让 preset 改动生效？",
 "memory-server 的 v2 导入为什么旧版本会截断到 500 字？",
 "dsh-bridge-tools 在 0.2.1 上重复注册工具怎么修？",
 "怎么确认生产上没有 running 会话再重启？",
 "抽取失败时怎么从 pending 恢复？",
 "literature 的 bias 约束怎么注入到系统提示？",
 "dsh-deepmemory 的测试集怎么跑？",
]
NOANS = [
 ("火星基地的氧气配额是多少？", ["火星基地","氧气配额"]),
 ("蓝鲸项目的服务器密码轮换周期？", ["蓝鲸项目","轮换"]),
 ("上周三客户会议的纪要结论？", ["客户会议","纪要"]),
 ("南极科考站柴油发电机型号？", ["南极","发电机"]),
 ("月球背面中继卫星的发射窗口？", ["月球","中转卫星"]),
 ("海底光缆的检修船编号？", ["海底光缆","检修船"]),
 ("紫金山天文台的望远镜口径？", ["紫金山","望远镜"]),
 ("公司食堂下周菜单的成本核算？", ["食堂","成本核算"]),
 ("杭州亚运会场馆的座位数？", ["亚运会","座位"]),
 ("深海探测器下潜深度的世界纪录保持者？", ["深海探测器","纪录"]),
]
def keywords(text):
    nouns, others, seen = [], [], set()
    for tok, flag in pseg.cut(text or ""):
        tok = tok.strip()
        if not tok or tok in STOP or tok in seen or len(tok) < 2: continue
        if flag.startswith("n") or re.match(r"^[A-Za-z][A-Za-z0-9_.\-]{3,}$", tok):
            seen.add(tok); nouns.append(tok)
        elif flag.startswith(("v","a")):
            seen.add(tok); others.append(tok)
    return (nouns + others)[:6]
def search(server, token, q, ws, k=8):
    body = {"query": q, "k": k}
    if ws: body["workspace_id"] = ws
    req = urllib.request.Request(server.rstrip("/") + "/v1/memories/search",
        data=json.dumps(body).encode(), headers={"Authorization":"Bearer "+token,"content-type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("results") or []
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True); ap.add_argument("--token-file", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:6230")
    ap.add_argument("--out", required=True); ap.add_argument("--n", type=int, default=45)
    ap.add_argument("--seed", type=int, default=7); ap.add_argument("--k", type=int, default=8)
    a = ap.parse_args(); random.seed(a.seed)
    token = open(a.token_file, encoding="utf-8").read().strip()
    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True); c.row_factory = sqlite3.Row
    rows = c.execute("SELECT id,content,type,scope,workspace_id FROM documents WHERE status='active' AND length(content) BETWEEN 30 AND 200 AND has_sensitive=0 AND scope IN ('workspace','global') AND workspace_id<>''").fetchall()
    random.shuffle(rows)
    cases, used = [], set()
    for r in rows:
        if len(cases) >= a.n: break
        kws = keywords(r["content"])
        kw = None
        for cand in kws[:4]:
            df = c.execute("SELECT COUNT(*) FROM documents WHERE status='active' AND content LIKE ?", ("%"+cand+"%",)).fetchone()[0]
            if 2 <= df <= 200: kw = cand; break
        if not kw or r["id"] in used: continue
        used.add(r["id"])
        typ = (r["type"] or "fact").strip() or "fact"
        q = (TMPL.get(typ) or TMPL["fact"])[0].format(kw=kw)
        cases.append({"id":"m-%03d"%(len(cases)+1),"kind":"memory","query":q,"seed_id":r["id"],"workspace_id":r["workspace_id"]})
    for i,t in enumerate(TASKS):
        cases.append({"id":"t-%03d"%(i+1),"kind":"task","query":t,"seed_id":None,"workspace_id":"bf4722bc-37d2-438f-b5dd-598d24a13cf5"})
    for i,(q,f) in enumerate(NOANS):
        cases.append({"id":"n-%03d"%(i+1),"kind":"no_answer","query":q,"seed_id":None,"facets":f,"workspace_id":""})
    out=[]
    for case in cases:
        try: rs = search(a.server, token, case["query"], case.get("workspace_id"), a.k)
        except Exception as e: rs=[]
        out.append({**case, "results":[{"id":x.get("id"),"content":(x.get("content") or "")[:120]} for x in rs]})
    with open(a.out,"w",encoding="utf-8") as f:
        for x in out: f.write(json.dumps(x,ensure_ascii=False)+"\n")
    print("cases:",len(out),"->",a.out)
if __name__=="__main__": main()
