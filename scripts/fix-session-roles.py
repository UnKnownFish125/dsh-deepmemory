#!/opt/AstrBot/venv/bin/python3
# -*- coding: utf-8 -*-
"""修复 DSH session 日志中 role:system 的 user/message 事件（改 role 为 user）。
格式保持：每行一个独立 zstd 帧。用法: fix-session-roles.py <file...>"""
import json
import os
import subprocess
import sys
import tempfile

import zstandard as zstd

dctx = zstd.ZstdDecompressor()
cctx = zstd.ZstdCompressor(level=3)


def decompress_all(path: str) -> bytes:
    with open(path, "rb") as fh:
        reader = dctx.stream_reader(fh)
        return reader.read()


def fix_lines(text: str):
    fixed = 0
    out = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            out.append(line)
            continue
        m = e.get("data", {}).get("message") if isinstance(e.get("data"), dict) else None
        if e.get("type") == "user/message" and isinstance(m, dict) and m.get("role") != "user":
            m["role"] = "user"
            line = json.dumps(e, ensure_ascii=False)
            fixed += 1
        out.append(line)
    return "\n".join(out) + "\n", fixed


def compress_lines(text: str) -> bytes:
    parts = []
    for line in text.split("\n"):
        if line:
            parts.append(cctx.compress((line + "\n").encode("utf-8")))
    return b"".join(parts)


def main():
    for path in sys.argv[1:]:
        if not os.path.isfile(path):
            print(f"跳过(不存在): {path}")
            continue
        text = decompress_all(path).decode("utf-8")
        new_text, fixed = fix_lines(text)
        total = new_text.count("\n")
        if fixed == 0:
            print(f"{path}: 无异常，跳过")
            continue
        data = compress_lines(new_text)
        tmp = path + ".fixing"
        with open(tmp, "wb") as fh:
            fh.write(data)
        # 验证
        check = decompress_all(tmp).decode("utf-8")
        _, still = fix_lines(check)
        if still != 0:
            print(f"{path}: 修复后仍有 {still} 处异常，保留原文件")
            os.remove(tmp)
            continue
        bak = path + ".bak-rolefix"
        if not os.path.exists(bak):
            os.rename(path, bak)
        os.rename(tmp, path)
        print(f"{path}: 修复 {fixed}/{total} 行，备份 {bak}")


if __name__ == "__main__":
    main()
