# 记忆召回评测（recall eval）

评估三件事：
1. **召回是否准确**：`hit_rate@k`、`Recall@k`、`MRR`
2. **召回数量是否合理**：`count_ok_rate`（结果条数是否落在每条 case 的 `expected_count_range`）
3. **记忆是否补全**：`facet_coverage`（每条 case 的必需事实片段在召回正文中的覆盖比例）

## 组成

| 文件 | 作用 |
|---|---|
| `build_recall_cases.py` | 从真实记忆库（只读 SQLite）生成测试集初稿：按 `workspace/global` 作用域采记忆，jieba 取稀有名词改写 query，附带期望 id、条数区间、facets |
| `recall_cases.jsonl` | 生成的测试集（当前 24 条 hit + 5 条 no_answer，来自测试库 deepseek-harness 工作区） |
| `run_recall_eval.py` | 走真实 HTTP `/v1/memories/search` 跑评测，输出 JSON 汇总 + Markdown 报告 |

## 用法

```bash
# 1) 生成/扩充测试集（只读 DB，不会写库）
/opt/AstrBot/venv/bin/python3 scripts/eval/build_recall_cases.py \
  --db /www/dsh-test-memory/data/memory.db \
  --out scripts/eval/recall_cases.jsonl --n 24

# 2) 跑评测（测试机 6240 或生产 6230 皆可，只读）
/opt/AstrBot/venv/bin/python3 scripts/eval/run_recall_eval.py \
  --server http://127.0.0.1:6240 \
  --token-file /www/dsh-test-home/.dsh-memory-api-token \
  --cases scripts/eval/recall_cases.jsonl --k 10 \
  --out-md /tmp/recall-report.md
```

## 指标口径与已知局限

- `expected_ids` = 种子记忆 + 同工作区含同一稀有名词的兄弟条；**不等于**“所有语义相关记忆”，
  因此 `noise_rate` 会偏高，应主要看 `hit_rate / recall / MRR / count_ok / facet_coverage`。
- 只取 `scope in (workspace, global)`：session 作用域记忆本来只在该会话可见，跨会话召回应走
  `memory_attach`，不纳入本基准。
- `no_answer` 用例用来测“不滥召回”，目标是 `count<=1`。
- 第二阶段的抽取完整性评测（gold facts → 库中存在性）尚未加入，见 TODO。
