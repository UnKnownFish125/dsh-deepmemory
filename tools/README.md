# deepmemory Tools 工具集

本目录存放 deepmemory 的**运维/迁移工具**（独立 CLI，不嵌入插件运行时）。

| 工具 | 用途 | 典型用法 |
|---|---|---|
| [`migrate_embedding.py`](migrate_embedding.py) | **向量模型迁移**：更换 embedding 模型（local fastembed 换模型 / local↔api / 换维度）后全库重嵌 + 重建 FAISS 与 BM25，含备份/验证/回滚提示 | `python3 migrate_embedding.py --server-dir <memory-server目录> --local-model BAAI/bge-m3` |
| [`check_cache_health.py`](check_cache_health.py) | **插件缓存命中率检查**：静态（注入文本稳定性：无易变元数据/稳定排序/回合内冻结）+ 动态（解析最近会话 usage，按 turn 分组判定"回合内是否击穿"） | `python3 check_cache_health.py --plugin /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js` |

## migrate_embedding.py（更换向量模型）

### 为什么需要
deepmemory 的语义检索依赖向量索引（FAISS `memory.faiss` + `dim.json` 维度元数据）。
换 embedding 模型（更好的模型/切 api）后，旧向量与新模型**不兼容**——必须用**新模型对全库记忆重新编码**并重建索引，否则检索质量退化（甚至维度错配）。

### 流程（与 server 的 rebuild_indexes 同链路，附加安全措施）
```
1. 备份  data/migrate-embedding-<时间戳>/{memory.faiss, dim.json}
2. 写入  新 embedding.* 配置（set_setting 与 /v1/config 同通道，自动清索引缓存）
3. 重嵌  server.rebuild_indexes()：全库 SELECT → 新模型编码 → 影子 FAISS → 原子替换 → BM25 重建（维度自适应）
4. 验证  抽样记忆向量检索应命中自身 + 输出新维度
5. 报告  迁移摘要；失败提示回滚
```

### 参数
| 参数 | 说明 |
|---|---|
| `--server-dir` | **必填**：memory-server 目录（含 server.py，工具复用其编码/索引实现） |
| `--provider` | `local`（fastembed 本地）/ `api`（OpenAI 兼容 /v1/embeddings） |
| `--local-model` | 本地模型名，如 `BAAI/bge-m3`（默认 bge-small-zh-v1.5，512 维） |
| `--api-base` / `--api-model` | provider=api 时的根地址与模型名 |
| `--dry-run` | 只加载新模型 warmup 一次（验证可加载+输出维度），不写配置不重建 |
| `--sample` | 验证抽样条数（默认 3） |

### 安全纪律
- **先测试机后生产**：测试机（6240）先 dry-run + 正式迁移→ 验证 → 再对生产（6230）执行
- 迁移前备份自动落盘（可回滚：`cp data/migrate-embedding-<ts>/* data/` + 还原配置）
- `--dry-run` 只做 warmup（不改配置），适合先确认新模型可加载/维度
- **禁止在生产运行中直接改 embedding 配置**：先停写流量或选低峰（重嵌期间旧索引仍在，但新向量写入前检索结果可能混维——重嵌完成后原子替换）

### 示例
```bash
# 测试机：换 bge-m3（本地）
/opt/AstrBot/venv/bin/python3 tools/migrate_embedding.py \
    --server-dir /www/dsh-test-memory --local-model BAAI/bge-m3

# 生产：dry-run 验证 api provider 可加载
/opt/AstrBot/venv/bin/python3 tools/migrate_embedding.py \
    --server-dir /www/deepmemory-v063-deploy/memory-server \
    --provider api --api-base https://api.openai.com/v1 --api-model text-embedding-3-small --dry-run
```

## check_cache_health.py（缓存命中率检查）

### 为什么需要
DeepSeek 前缀缓存对**前缀字节稳定性**极其敏感（前缀缓存击穿 = 每步全价）。本工具是**更新生产前的硬 gate**：插件任何改动、同步生产前先跑它，PASS 才允许。

### 两条腿
1. **静态健康**：解析 plugin-v3.js——注入文本无易变元数据（`[i0.xx]`/日期/`topic:`）、稳定排序（by id）、回合内冻结守卫（userCount）存在
2. **动态健康**：解析最近会话 `assistant/message` 的 usage（cacheReadTokens/inputTokens），按 **turn 分组**——每组首请求允许 miss（回合首刷新），**组内后续步骤 miss = 真击穿**；会话文件早于插件生效时间的旧记录标 `stale` 仅参考

### 判定
- 通过：静态无违规 + 所有活跃会话组内无击穿，命中率≥90%
- 退出码：`0`=PASS 可同步生产；`1`=FAIL 禁止同步
