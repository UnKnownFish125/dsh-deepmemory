# 记忆召回评测 · 扩容版（145 例 + 双复核一致性）

- 时间：2026-10-24
- 数据：生产记忆库（只读，34k docs）
- 用例：`scripts/eval/recall_cases_160.jsonl`（120 memory + 15 task + 10 no_answer = 145）
- 判定：agent 独立复核员（deepseek-flash）全量判定；lead 抽 20 条独立判定做一致性校验
- 口径：useful = 能直接引用其事实/决策/规则/路径/命令来回答 query；泛化偏好不计

## 核心指标（145 例，独立复核员）

| 维度 | 指标 | 值 |
|---|---|---|
| 召回准确（memory） | seed_hit | **0.450**（54/120） |
| 召回准确（memory） | precision（有用/召回） | **0.440** |
| 召回准确（task） | 任务至少 1 条有用 | **0.600**（9/15） |
| 召回准确（task） | precision | **0.206** |
| 数量合理 | count_ok | **0.559**（ok 97 / too_many 30 / too_few 13 / over_recall 5） |
| 不滥召回 | no_answer 通过 | **0.500**（5/10） |

## 双复核一致性（20 条重叠）

| 指标 | 一致率 |
|---|---|
| useful 集合 Jaccard | **0.90** |
| seed_hit 判定 | **1.00** |
| count_judgment | **0.75** |

说明：两个独立判定对“哪条有用”高度一致，说明评测口径稳定、可复现。

## 系统性发现

1. **泛化偏好污染**：`1 / 8166 / 13693 / 14105 / 12105 / 31781 / 163 / 32098` 这组“喜欢中文 / 先方案后实施 / 修 bug 先诊断 / 直接弄好”会在
   “用户对「X」的偏好/要求”类 query 里占满 top-8，与主题词零命中，是 too_many（30 条）的主因。
2. **零结果漏召回**：沙箱（m-006/m-039）、slots、文件名、生命周期、库存等 12 条 0 结果，seed 完全没回来。
3. **任务型尤其差**：task precision 0.206、覆盖 0.6；`t-002 重启前检查` 没召回真正契约（33770/34348），
   `t-005 bias 写入`、`t-013 pending 恢复`、`t-015 评测跑法` 全漏或被噪声占满。
4. **硬事实型明显更好**：水泥、CPOF、pkill、GitHub、reasoning 等查询 seed_hit 基本为 true。
5. **无答案题仍有一半硬召回**：凭据规则被当成“蓝鲸项目密码轮换”的答案。

## 复现

```bash
/opt/AstrBot/venv/bin/python3 scripts/eval/build_review_set.py \
  --db /www/deepmemory-v063-deploy/memory-server/data/memory.db \
  --token-file /www/dsh/home/.dsh-memory-api-token \
  --server http://127.0.0.1:6230 --out /tmp/review_set.jsonl --n 120 --k 8
# labels 与打分见本次记录（/tmp/labels_sub.jsonl、/tmp/labels_me.jsonl）
```

## Kimi 第三复核（待回收）

`scripts/eval/review/kimi_cases_30.json` + `kimi_instructions.md` 已备好，由用户转交 Kimi 复核 30 条；
回收 labels 后与上表做三方一致性对比。

## Kimi 第三复核（30 条重叠，2026-10-24 回收）

- 输入：`scripts/eval/review/kimi_cases_30.json`；Kimi 输出：`scripts/eval/review/kimi_labels_30.jsonl`
- 与独立复核员在同一 30 条上对比：

| 指标 | Kimi | 复核员 | 一致性 |
|---|---|---|---|
| precision | 0.500 | 0.529 | Jaccard **0.843** |
| seed_hit | 0.500 | 0.500 | **1.000** |
| count_ok | 0.767 | 0.667 | **0.900** |

- seed_hit 自洽检查：0 异常（`seed_id ∈ useful_ids` 与 seed_hit 完全一致）
- 分歧最大：m-113 / m-064 / m-056（useful 集合完全相反，Jaccard 0）、m-023（0.5）、m-100（0.33）
  - 这三条都是“有结果但未召回种子”的边界 case：Kimi 认为其中若干条对回答 query 有辅助价值，复核员按“必须直接可引用”判为无用。
  - 说明：**边界判定（辅助 vs 直接可用）是剩余 15% 分歧的主要来源**，seed_hit 本身高度稳定（1.0）。

## 三方结论

- 评测口径可复现：seed_hit 三方一致率 1.0；useful 集合两两 Jaccard 0.84–0.90；
- 145 例主指标以独立复核员为准（seed_hit 0.45、task precision 0.21、count_ok 0.56、no_answer 0.50）；
- 下一阶段优先修：泛化偏好降权 > 空结果阈值 > 任务型重排。
