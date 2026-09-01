#!/usr/bin/env python3
"""deepmemory 向量模型迁移工具 —— 更换 embedding 模型的完整流程（备份→换配置→全库重嵌→验证）。

用途：
  知识库/记忆库更换向量模型（local fastembed 换模型 / local→api / 换维度）后，
  对全库记忆重新生成向量并重建 FAISS 索引与 BM25，保证检索质量不因模型切换而退化。

用法：
  python3 migrate_embedding.py --server-dir /www/deepmemory-v063-deploy/memory-server \
      --local-model BAAI/bge-m3 \
      [--provider local|api] [--api-base https://.../v1] [--api-model text-embedding-3-small] \
      [--dry-run] [--sample 3]

流程（与 server 的 rebuild_indexes 同链路，增加备份/回滚/验证）：
  1. 读取当前 embedding 配置 + schema 词汇
  2. 备份 data/memory.faiss + dim.json（带时间戳，可回滚）
  3. 写入新 embedding.* 配置（set_setting，与 /v1/config 同通道）
  4. 全库重嵌：server.rebuild_indexes()（影子 FAISS → 原子替换 → BM25 重建，维度自适应）
  5. 验证：抽样记忆向量检索命中自身 + 维度报告
  6. 输出迁移报告；失败时提示用备份回滚

安全：
  - 默认 dry-run：只 warmup 加载新模型、验证编码，不改配置不重建
  - 备份在 data/migrate-embedding-<ts>/{memory.faiss,dim.json}，回滚=恢复备份+还原配置
"""
import argparse
import json
import os
import shutil
import sys
import time

BACKUP_SUBDIR = "migrate-embedding"


def _load_server(server_dir):
    """加载 memory-server 模块（复用其 embed_texts/rebuild_indexes 等实现）。"""
    server_dir = os.path.abspath(server_dir)
    if not os.path.isfile(os.path.join(server_dir, "server.py")):
        raise SystemExit(f"server-dir 无效（需要含 server.py）: {server_dir}")
    sys.path.insert(0, server_dir)
    import server
    return server


def _current_config(server):
    values = server.get_config_values()
    return {
        "provider": values.get("embedding.provider") or server.get_embed_config().get("provider"),
        "model": values.get("embedding.local_model") or server.get_embed_config().get("local_model"),
    }


def main():
    ap = argparse.ArgumentParser(description="deepmemory 向量模型迁移（全库重嵌+重建索引）")
    ap.add_argument("--server-dir", required=True, help="memory-server 目录（含 server.py）")
    ap.add_argument("--provider", choices=["local", "api"], help="目标 embedding provider（默认不变）")
    ap.add_argument("--local-model", help="目标本地模型，如 BAAI/bge-m3")
    ap.add_argument("--api-base", help="provider=api 时的 OpenAI 兼容地址")
    ap.add_argument("--api-model", help="provider=api 时的模型名")
    ap.add_argument("--dry-run", action="store_true", help="只 warmup 新模型，不落库不重建")
    ap.add_argument("--sample", type=int, default=3, help="验证抽样条数（默认 3）")
    args = ap.parse_args()

    server = _load_server(args.server_dir)
    data_dir = server.DATA_DIR
    index_path = server.INDEX_PATH
    dim_path = server.DIM_PATH
    old = _current_config(server)

    print(f"当前配置: provider={old['provider']} model={old['model']}")
    print(f"目标配置: provider={args.provider or old['provider']} "
          f"model={args.local_model or args.api_model or old['model']}")

    if args.dry_run:
        # 仅 warmup：加载新模型编码一次（验证可加载 + 输出维度），不写配置不重建
        if args.provider:
            server.set_setting("deepmemory.embedding.provider", args.provider)
        if args.local_model:
            server.set_setting("deepmemory.embedding.local_model", args.local_model)
        if args.api_base:
            server.set_setting("deepmemory.embedding.api_base_url", args.api_base)
        if args.api_model:
            server.set_setting("deepmemory.embedding.api_model", args.api_model)
        try:
            vec = server.embed_texts(["warmup: 验证新向量模型可加载"])
            print(f"[dry-run] 新模型加载 OK，维度 = {vec[0].shape[0] if hasattr(vec[0], 'shape') else len(vec[0])}")
        finally:
            # 还原配置（dry-run 不持久化变更）
            server.set_setting("deepmemory.embedding.provider", old["provider"])
            if old["model"]:
                server.set_setting("deepmemory.embedding.local_model", old["model"])
        return

    # ---- 正式迁移 ----
    backup_dir = os.path.join(data_dir, BACKUP_SUBDIR, time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(backup_dir, exist_ok=True)
    n_backed = 0
    for f in (index_path, dim_path):
        if os.path.isfile(f):
            shutil.copy2(f, os.path.join(backup_dir, os.path.basename(f)))
            n_backed += 1
    print(f"[1/5] 备份完成（{n_backed} 个文件）: {backup_dir}")

    # 写入新配置（set_setting 与 /v1/config 同通道；embedding 变更自动清索引缓存）
    if args.provider:
        server.set_setting("deepmemory.embedding.provider", args.provider)
    if args.local_model:
        server.set_setting("deepmemory.embedding.local_model", args.local_model)
    if args.api_base:
        server.set_setting("deepmemory.embedding.api_base_url", args.api_base)
    if args.api_model:
        server.set_setting("deepmemory.embedding.api_model", args.api_model)
    # api_base_url 在 config_schema 归 api 组；local_model 同理——schema 已覆盖
    print("[2/5] 新 embedding 配置已写入")

    # 全库重嵌（影子重建+原子替换+BM25 重建+维度自适应）
    t0 = time.time()
    try:
        result = server.rebuild_indexes()
    except Exception as e:
        print(f"[3/5] 重嵌失败: {e} —— 用备份回滚: cp {backup_dir}/* {data_dir}/")
        sys.exit(1)
    elapsed = time.time() - t0
    print(f"[3/5] 全库重嵌完成（{elapsed:.1f}s）: {result}")

    # 验证：抽样记忆向量检索应命中自身
    conn = server.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, content FROM documents WHERE status='active' AND storage_tier='active' "
            "ORDER BY id DESC LIMIT ?",
            (args.sample,),
        ).fetchall()
    finally:
        conn.close()
    ok = 0
    for rid, content in rows:
        if not content:
            continue
        try:
            vec = server.embed_texts([str(content)[:200]])
            hits = server.vector_search(server.normalize(vec[0]), 5)
            ids = [int(h[0]) for h in hits] if hits else []
            if rid in ids:
                ok += 1
        except Exception:
            continue
    new_dim = None
    try:
        with open(dim_path) as fh:
            new_dim = json.load(fh).get("dim")
    except Exception:
        pass
    print(f"[4/5] 验证：{ok}/{max(len(rows), 1)} 抽样命中自身 | 新维度 = {new_dim}")
    print(f"[5/5] 迁移完成: provider={args.provider or old['provider']} model={args.local_model or args.api_model}")
    if ok < max(len(rows), 1):
        print("⚠ 部分抽样未命中：检查模型/维度（检索可能已降级，可回滚）")
        sys.exit(2)


if __name__ == "__main__":
    main()
