# 记忆召回质量复核任务（Kimi）

请对随附 JSON 中的 30 条记忆召回结果做**独立判定**，判断每条召回对回答该 query / 执行该任务是否有**直接帮助**（不是“主题沾边”）。

## 输入格式（kimi_cases_30.json）
每个元素：
```json
{"id":"m-123","kind":"memory","query":"...","seed_id":456,"results":[{"id":789,"content":"..."}]}
```
- `results` 是记忆库按 query 返回的 top-8（content 已截断到 100 字）。
- `seed_id` 是这条 query 对应的“种子记忆”id（可能为 null）。

## 判定规则
1. `useful_ids`：仅包含**能直接用于回答该问题**的召回 id。泛泛偏好（如“喜欢中文”“先方案后实施”）不算，除非 query 就是在问偏好。
2. `seed_hit`：`seed_id` 出现在 `useful_ids` 里才为 true；kind=task 时填 null。
3. `count_judgment`：
   - 0 条结果 → `"too_few"`
   - `len(results) > max(5, 2*len(useful_ids))` → `"too_many"`
   - 其他 → `"ok"`
4. kind=no_answer（库里不应存在该主题）：`useful_ids=[]`、`seed_hit=null`；0 条 → ok，有任何结果 → `"over_recall"`。
5. 严格、保守；不确定的不要计入 useful。

## 输出格式（严格 JSONL，30 行）
```json
{"id":"m-123","useful_ids":[789],"seed_hit":true,"count_judgment":"ok"}
```

完成后把 labels 文件发回；我们会与另一个独立复核员比对一致性（Cohen's kappa / 一致率）后并入总报告。
