# v0.4 语义模型改造设计稿（codex 三轮设计——待审）

> model codex-auto-review/115K tokens/仅设计
> 解决二轮 P0 三缺陷：A 无"当前可信事实" B 相似=事实相同 C 固化次数=确认次数

## 一、数据变更
```sql
CREATE TABLE assertions(
 id INTEGER PRIMARY KEY, memory_id INT NOT NULL REFERENCES documents(id),
 subject_id TEXT NOT NULL, predicate TEXT NOT NULL,
 value_json TEXT NOT NULL, polarity TEXT DEFAULT 'positive',
 conditions_json TEXT DEFAULT '{}',
 valid_from REAL, valid_to REAL, recorded_at REAL NOT NULL,
 status TEXT DEFAULT 'unverified',   -- unverified/candidate/adopted/disputed/revoked/superseded
 conflict_group TEXT, supersedes_id INT REFERENCES assertions(id)
);
CREATE TABLE assertion_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, assertion_id INT NOT NULL REFERENCES assertions(id),
 kind TEXT NOT NULL,       -- support/contradict/confirm/promote/revoke
 actor TEXT NOT NULL, origin_id TEXT NOT NULL,
 source_id INT REFERENCES sources(id), target_event_id INT, created_at REAL NOT NULL,
 UNIQUE(assertion_id,kind,origin_id)
);
```
- 断言版本 append-only（修订→memory_revisions 既有）；valid_* 半开区间（≠recorded_at 或 event_time）
- 索引：主体/属性/状态/有效期/冲突组；禁止替代环+非法区间
- 迁移幂等，失败阻止启动

## 二、检索语义
- `search/recall` 默认 **mode=current**：过滤后仅取 `status=adopted`+未争议+未撤销/替代+当前在有效期内；decision 另须 decision_status=adopted；history/investigate 才返回历史/争议
- **断言识别**：主体+属性+条件显式匹配；值/极性冲突→同 conflict_group（未裁决双方不注入）；**相似度仅发现候选**——不直接覆盖/聚合/归档；比较前同一隔离域
- 缓存：键加 query mode/过滤项/`semantic_generation`；确认/撤销/替代递增 generation+清缓存；有效期边界到期失效
- **不变**：插件回合冻结/前缀稳定/注入格式；轨 A bias 恒并也过状态+有效期门禁

## 三、确认机制（C）
- 晋升：**2 条不同 origin_id 的独立确认事件 + 服务端鉴权 actor 执行 promote**（重复抽取/转述/导入不计数；忽略 body 的 confirmed/user_id）
- 撤销事件指向确认/规则版本；关键证据撤销→断言降 disputed+依赖规则暂停；事务内事件/状态/revision 同写
- rule_crystallized 仅发布投影（规则文件按 adopted 重建）

## 四、兼容迁移
- 453 存量：保留 ID/原文/scope/library/tier；disputed=1 标 disputed；其余 **unverified**（不自动 adopted；有效期 NULL 不推断——current 召回可能暂时减少——人工确认时补）
- 旧相似合并/rule_crystallized=1 **不算确认**；旧注入 JSON 原样（新字段可忽略）
- export-archive 保留旧结构+since，新增断言状态/事件游标/撤销记录；脱敏/vault 保持
- 轨 A 按新门禁；轨 B 副本/已加载 L0 不即时回撤——按事件游标重载 + source_memory_id 抑制撤销项

## 五、实施拆分
| 阶段 | 内容 |
|---|---|
| **P0** | assertions 表+状态/有效期门禁+存量标记+缓存 generation；**关闭**相似覆盖/自动归档/"出现两次即候选" |
| **P1** | assertion_events+身份校验+确认/撤销 API+规则投影 |
| P2 | 冲突组/替代链、多断言投影、增量导出游标、轨 B 重载、453 条复核+阈值反例标定 |
| 验收 | 过期/争议零注入；允许-禁止/生产-测试不合并；重抽取不增确认；撤销不复活；回合内注入字节稳定 |

## 待审问题（给 GLM/用户）
1. P0 直接关"相似覆盖/归档/两次候选"——**存量注入量会降**（unverified 不注入——**可接受？**（语义正确性优先于注入量）
2. 确认事件 origin_id 从哪来（服务端会话/消息身份——**已有 trusted user env**）——单机单用户场景确认流程（人工确认 API？）
3. 实施规模（P0 1-2 天/P1 2 天/P2 分阶段）——**是否与 workbench P0 并行排期**
