# 知识库"看不到"排查报告（2026-09-06）

> 现象：deepmemory 侧数据/API 均正常（knowledge 101-102 条），但用户在 WebUI 看不到知识。
> 结论先行：**后端链路全通，疑似前端（客户端缓存/或 workspace 参数），非数据缺失。**

## ① 数据层（✅ 正常）
- `literature.db`：`knowledge_items` **102 条**（deleted_at IS NULL 101）；其中 archived=1、bias 4 / core 1 / runtime 97
- `workspace_id` 全为 **'deepseek-harness'**（统一——无过滤错配）

## ② API 层（✅ 正常）
| 端点 | 结果 |
|---|---|
| `GET 6260 /v1/literature/knowledge-browse` | ✅ 200 —— `{"items":[{"id":106,"concept":"schema分阶段实施",...` 列表返回 |
| `GET 6260 /v1/literature/knowledge-count` | ✅ 200 —— `{"count":101}` |
| `GET 3081 /lit-api/v1/literature/knowledge-browse`（前端同源通道） | ✅ 200 —— 同样返回 items |
| 3081 /lit-api knowledge-count | ✅ 200 —— 101 |

## ③ 前端（web-plugin——有视图，疑点在加载参数/缓存）
- `client.js` **views 定义含知识 tab**：`{ id:'knowledge', label:'🧠 知识', component: KnowledgeView }`（:910）
- KnowledgeView 列表调用（:852）：`/knowledge-browse?workspace_id=<ws>&archived=false&k=1000`
- KnowledgeDetail（:578）：`api(pathFor('knowledge', kid, workspaceId))`——详情端

### 疑似根因（按概率排序）
1. **浏览器缓存旧 client.js**（旧版无 🧠 知识 tab）——**硬刷新（Ctrl+Shift+R）**试
2. **前端 ws 参数**：列表调用传 `workspace_id=<ws>`——若前端当前工作区显示≠'deepseek-harness'，101 条会被过滤（后端已验证 harness 全配）——**确认面板上方工作区标识**
3. knowledge 详情 pathFor：`/knowledge/<id>`（isdigit 已修——旧 bundle 可能仍走坏路由）——同上（缓存）

## ④ 建议（literature 侧/或用户操作）
1. 用户：**硬刷新页面**（Ctrl+Shift+R）→ 点「🧠 知识」tab → 确认工作区=deepseek-harness
2. 若仍空：开发端 console 看 `/knowledge-browse` 请求的 `workspace_id` 值与 HTTP 状态（200/过滤）
3. literature 侧可加兜底：knowledge-browse 当 workspace_id 无效/空时不强制过滤（或返回"切换工作区"提示）

## 附：与 deepmemory 无关
- 记忆侧 107 条 unverified 断言为 P0 语义策略（无确认不进语义检索）——**这是记忆注入策略，不影响文献库知识显示**
