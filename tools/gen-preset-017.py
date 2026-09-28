#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen-preset-017.py — generate a DSH 0.1.7 declarative agent-preset declaration
from a 0.1.5-era `<preset-dir>/agent.cordis.yml` entry list.

0.1.5  : the preset was a directory `$DSH_HOME/.agent-presets/<id>/agent.cordis.yml`
         discovered by `@deepseek-ai/dsh-agent-presets` (plural) `scanRoot()`+readdir.
0.1.7  : the preset is an ordinary Cordis composition row
         `- id: preset-<id> / name: '@deepseek-ai/dsh-agent-preset' / config: PresetDefinition`.
         Nothing scans directories; the registry only knows what a composition declares.

This script does a purely TEXTUAL transform (comments and `!!js` tags survive):

  * indent the whole entry list by N spaces under `config.plugins:`
  * rewrite the preset-local relative plugin specifiers to absolute paths
    (in 0.1.7 the child rows resolve relative specifiers against the *declaring*
     entry's baseUrl — the profile dir — not against the preset dir)
  * `0.1.5-only` row substitutions that 0.1.7 no longer ships (see ROW_SUBSTITUTIONS)

Output: a `- insert:` patch-list fragment that can be appended to a profile's
`cordis.patch.yml`, or to a profile-local patch file.

-------------------------------------------------------------------------------
输入 / 输出约定（2026-09-28 入库时补记）
-------------------------------------------------------------------------------

输入
  <preset-dir>/agent.cordis.yml
      0.1.5 时代的 preset 入口清单：**顶层 YAML 数组**，每项是一个 EntryOptions
      （`- id: .. / name: .. / config: .. / disabled: ..`）。`--source` 可换文件名。
      `--preset-dir` 同时充当相对 specifier 的解析基准（相对路径按它归一化）。

输出
  一段可直接**追加**到 `profiles/<name>/cordis.patch.yml` 的 `- insert:` 片段，
  形如：

      - insert:
          - id: preset-<id>
            name: '@deepseek-ai/dsh-agent-preset'
            config:
              id: <id>
              name: ...            # --name
              description: ...     # --description
              order: <N>           # --order，默认 50
              plugins:             # 原清单整体缩进 10 空格挂到此处
                - id: persona
                  ...

  默认打到 stdout；`--out FILE` 写文件（stderr 报字节数）。

变换是**纯文本**的：注释与 `!!js` 标签原样保留，不经过 YAML round-trip
（避免 PyYAML 改写格式）。共三步：① 绝对化 preset 本地相对 specifier；
② 0.1.5 专属行替换（ROW_SUBSTITUTIONS / ID_SUBSTITUTIONS）；
③ 丢弃 0.1.7 schema 已删除的键（DROP_KEYS）。

-------------------------------------------------------------------------------
已知限制（重要）
-------------------------------------------------------------------------------

* **只绝对化 `../` 开头的 `name:`，不处理 `./` 开头的**（正则只匹配 `../` 前缀）。
  0.1.7 下子插件行的相对路径基准是「**声明行所在的 profile 目录**」而不是 preset
  目录，所以两者都会 404，但 `./x.js` 必须**手工**改成绝对路径（或先改写成
  `../<preset-dir-name>/x.js` 再跑生成器）。实测踩坑：`harness-memory-blank`
  的 `./plugin/plugin.js` 就是手改的。
* 只认 `name:` 后跟单引号或双引号包裹的 specifier；裸值/模板字符串不会被改写。
* `DROP_KEYS` 的块删除依赖 `- id: ` 作为行首锚点，对嵌套 group 内的同名行可能过度
  匹配；用完请 diff 人工确认。
* 生成头注释里写死了生成器路径 `/www/scripts/gen-preset-017.py`（保留原样即可，
  只影响注释文字，不影响语义）。
* 生成器不负责**重启与验证**：追加后 `cordis.patch.yml` 只是被增量热挂载，
  **完整一致性仍要重启**（详见 `docs/preset-017-migration.md`）。

Usage:
  gen-preset-017.py --preset-dir DIR --id ID [--name N] [--description D] [--order N] [--out FILE]
  gen-preset-017.py --preset-dir DIR --id ID                 # print to stdout

示例（0.1.7 适配时的真实用法）:
  ./tools/gen-preset-017.py \
      --preset-dir "$DSH_HOME/.agent-presets/harness-memory-task" \
      --id harness-memory-task --order 50 \
      --name '任务型 Agent（长期记忆 / deepmemory）' \
      --description '带任务看板、过程记忆与委派的任务型编码 agent（deepmemory 载体）' \
      --out /tmp/preset-harness-memory-task.yml
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# 0.1.5 row name -> (0.1.7 row name, replacement config block or None)
# `@deepseek-ai/dsh-workflow-worker-thread` was retired; 0.1.7 ships
# `@deepseek-ai/dsh-workflow-ptc` (the spawn-based engine the builtin presets mount).
ROW_SUBSTITUTIONS = {
    "@deepseek-ai/dsh-workflow-worker-thread": "@deepseek-ai/dsh-workflow-ptc",
}

# Row-id renames that accompany a ROW_SUBSTITUTIONS entry (ids are local to the
# entry tree; renaming keeps the migrated file readable).
ID_SUBSTITUTIONS = {
    "workflow-worker-thread": "workflow-ptc",
}

# `dsh-persona` dropped its 0.1.5 `text:` field; only prefix/suffix/complete/
# includeRuntimeContext remain.
DROP_KEYS = {"- id: persona": ["text"]}


def transform_entry_list(text: str, preset_dir: str, indent: int) -> str:
    lines = text.splitlines()

    # 1) absolute-ise preset-local relative specifiers -----------------------
    out = []
    for line in lines:
        m = re.match(r"^(\s*name:\s*)(['\"])(\.\./[^'\"]+)\2\s*$", line)
        if m:
            rel = m.group(3)
            abs_path = os.path.normpath(os.path.join(preset_dir, rel))
            line = "%s%s%s%s" % (m.group(1), m.group(2), abs_path, m.group(2))
        else:
            m = re.match(r"^(\s*name:\s*)(\$\{?PRESET_DIR\}?/)(.+)$", line)
        out.append(line)
    lines = out

    # 2) row substitutions ---------------------------------------------------
    out = []
    for line in lines:
        m = re.match(r"^(\s*name:\s*)(['\"])(@deepseek-ai/[A-Za-z0-9_./-]+)\2\s*$", line)
        if m and m.group(3) in ROW_SUBSTITUTIONS:
            line = "%s%s%s%s" % (m.group(1), m.group(2), ROW_SUBSTITUTIONS[m.group(3)], m.group(2))
        else:
            m = re.match(r"^(\s*- id:\s*)([A-Za-z0-9_.-]+)\s*$", line)
            if m and m.group(2) in ID_SUBSTITUTIONS:
                line = "%s%s" % (m.group(1), ID_SUBSTITUTIONS[m.group(2)])
        out.append(line)
    lines = out

    body = "\n".join(lines)

    # 3) drop keys the 0.1.7 schema no longer declares ----------------------
    for anchor, keys in DROP_KEYS.items():
        # find the row block and remove `key: ...` (scalar or block) inside it
        start = body.find(anchor)
        if start == -1:
            continue
        # block runs until the next top-level row marker at the same indent
        rest = body[start + len(anchor):]
        nxt = re.search(r"\n(?=- id: )", rest)
        block = rest[: nxt.start()] if nxt else rest
        for key in keys:
            block = re.sub(r"\n[ \t]+%s:.*(?:\n(?=[ \t]{6,}\S)[^\n]*)*" % re.escape(key), "", block)
        body = body[: start + len(anchor)] + block + (rest[nxt.start():] if nxt else "")

    # 4) indent under `plugins:` --------------------------------------------
    pad = " " * indent
    indented = "\n".join((pad + ln) if ln.strip() else "" for ln in body.splitlines())
    return indented.rstrip() + "\n"


def build(entry_list: str, preset_id: str, name: str | None, description: str | None,
          order: int | None, preset_dir: str) -> str:
    head = [
        "# ── agent preset %s (DSH 0.1.7 declarative form) ─────────────────" % preset_id,
        "# Generated by /www/scripts/gen-preset-017.py from",
        "#   %s" % os.path.join(preset_dir, "agent.cordis.yml"),
        "# 0.1.7 no longer scans $DSH_HOME/.agent-presets; a preset is a normal",
        "# Cordis row declaring @deepseek-ai/dsh-agent-preset with config.plugins.",
        "- insert:",
        "    - id: preset-%s" % preset_id,
        "      name: '@deepseek-ai/dsh-agent-preset'",
        "      config:",
        "        id: %s" % preset_id,
    ]
    if name:
        head.append("        name: %s" % yaml_scalar(name))
    if description:
        head.append("        description: %s" % yaml_scalar(description))
    if order is not None:
        head.append("        order: %d" % order)
    head.append("        plugins:")
    return "\n".join(head) + "\n" + transform_entry_list(entry_list, preset_dir, 10)


def yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./@+-]+", value):
        return value
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset-dir", required=True,
                    help="directory holding the 0.1.5 agent.cordis.yml (its dirname is the relative-specifier base)")
    ap.add_argument("--id", required=True, help="preset id (the registry key)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--description", default=None)
    ap.add_argument("--order", type=int, default=50)
    ap.add_argument("--source", default="agent.cordis.yml")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    src = os.path.join(args.preset_dir, args.source)
    with open(src, "r", encoding="utf-8") as fh:
        entry_list = fh.read()

    text = build(entry_list, args.id, args.name, args.description, args.order, args.preset_dir)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("wrote %s (%d bytes)\n" % (args.out, len(text.encode())))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
