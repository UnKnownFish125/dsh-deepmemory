# Contributing

感谢关注 dsh-deepmemory。参与开发前请阅读以下约定：

## 验证

- 每次改动合并前必须跑 `bash scripts/verify.sh`（空白试验机三层验证：server 冒烟 / client 渲染 / preset 插件）。
- 验证在临时目录、随机端口、模拟环境里执行，不触碰生产 data、端口与 dsh 进程。
- 任何一项 FAIL 都禁止进入生产，install.sh 会再次强制验证。

## 分支与发布

- 功能开发在独立分支进行，合入 main 前先通过 verify。
- 发布使用语义化 tag（如 v0.2.1）；小修复打 .x 尾标签，大版本打主版本号。
- 不向 main 直接提交未验证的 WIP。

## 范围

- `memory-server/`：记忆数据层与 API。
- `agent-preset/`：task/daily/blank 三套 preset 与预算配置契约。
- `web-plugin/`：Web UI 插件。
- 修改请保持现有 API、表结构与配置键兼容（P1 约束）。
