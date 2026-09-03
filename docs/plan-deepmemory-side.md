# deepmemory 侧改动方案（供 GLM 审核）

> 背景：literature（知识库）从 deepmemory 拉取"记忆+原始来源"做原料归档（对接契约 v0.3.1+v0.4）；
> deepmemory 自身记忆注入架构已升级（三层注入）；本方案=deepmemory 侧的改动清单。
> 生产基线：6240 测试 / 6230 生产 同步推进（先测后产）。

## 0. 已实施（供审核确认，勿重复实施）
| 项 | 状态 | 提交 |
|---|---|---|
| R2 原文分段（插件 2000→32000 + server 8K 分段/truncated） | ✅ 生产+测试机 | 0c45d60 |
| R1+R5+R6 export-archive（批量导出/脱敏+protected/since/workspace-OR-global） | ✅ | 6800926 |
| R3 归档 source_refs 列表化（sources.id 全含） | ✅ | 6800926 |
| L3 行为规则按需注入（操作意图词→[操作规则] 预算外块） | ✅ | 2b72ed1 |
| extract 单一性铁律 + rule 类型/trigger 打标 | ✅ | cb2676d |
| 契约 v0.4（原文分段/三层注入/单一性/夜间加工） | ✅ 文档 | 0117072 |

## 1. 待实施改动（本次核心）

### 1.1 G1 轨 A：bias 库恒并（记忆注入增强）
- **问题**：bias（约束/契约类）偏置依赖**语义召回**——向量不匹配时**漏召回** → 约束不可见（风险：违反偏置约定）
- **改动**：L2 刷新时，除 12-15 条语义召回外，**bias 库全量恒并**（≤6 条，固定按 importance 排序）——**不依赖向量匹配**，稳定注入 `[规则与偏好]`（合并同类分组）
- **缓存纪律**：bias 恒并段**回合内冻结**（同 L2——user 消息才刷新）；排序稳定（importance 固定）；不引入易变字段
- **文件**：`plugin-v3.js` refreshMemoryCache（检索段加 library=bias 的 fixed fetch）
- **影响**：bias 3 条 → 注入 +3 条（预算内：走`INJECT_BUDGET_CHARS` 总裁剪——bias 段**优先保留**（preference 类在预算裁剪序前）

### 1.2 记忆向量模型（迁移工具已就绪，未执行）
- **工具**：`tools/migrate_embedding.py`（备份/重建/验证/dry-run）已就绪；dry-run 验证 `jinaai/jina-embeddings-v2-base-zh`（768 维）可加载
- **决策点**：是否执行换模型（bge-small-zh 512 → jina 768）？
  - 支持：中文专项更强；迁移工具无损（备份+影子重建+原子替换）
  - 反对：**与 literature 知识向量（同样计划用 jina）分库**——若两者一致更统一（但索引独立）
  - **建议**：与 literature 知识向量**同时**换（统一模型，分索引），或在 literature 上线后按需
- **注意**：deepmemory 记忆向量与 literature 知识向量**绝不共享索引/混掺**（两套独立）

### 1.3 无需改动项（确认保持）
- workspace 隔离 + `scope='global'` 恒可见（文献检索前提——export/召回同语义）
- G5 doc-link（记忆↔文献溯源）已就绪；`libraries/list/graph/search 透传` 已就绪（kb 工具链依赖——保持兼容）

## 2. 验收
- bias 恒并：bias 库记忆在任意 query 下均注入（抽样验证）
- 缓存：bias 段注入后回合内命中率不回退（check_cache_health PASS）
- 语义：kb_query（deepmemory 侧透传路径）不因 bias 恒并改变非 bias 结果

## 3. 风险与回滚
- bias 恒并 +3 条注入 → 预算裁剪可能挤掉边界记忆（可接受：bias 优先）
- 回滚：plugin 单文件回退（.bak-* 已有）
