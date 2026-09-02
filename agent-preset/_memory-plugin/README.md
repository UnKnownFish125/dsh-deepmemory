# _memory-plugin（生产加载路径）

DSH 生产实际加载路径：`/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js`（agent presets 安装时以 `_` 前缀实例化）。

- `plugin-v3.js` 与本目录上级 `memory-plugin/plugin-v3.js` **保持逐字节一致**（部署时复制）
- 修改任一版本后**必须同步另一份**并跑 `node --check` + `node --input-type=module -e "await import(...)"`（ESM 语义校验，防 CJS 宽松模式漏检）
- 历史：四击事故中 ②④（漏 order/非 async await）均为"检查工具语义≠运行时语义"导致——ESM 冒烟为强制门禁
