#!/usr/bin/env python3
"""deepmemory 插件缓存命中率健康检查（更新生产前的硬 gate，防缓存击穿再发生）。

用法:
  python3 check_cache_health.py \
      --plugin /www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js \
      --sessions /www/dsh/home/sessions \
      --min-rate 0.90 \
      [--recent 15] [--json]

检查两条腿:
  1. 静态健康: plugin-v3.js 注入文本必须稳定(无易变元数据/稳定排序/回合内冻结)
  2. 动态健康: 最近会话 usage(cacheReadTokens/inputTokens) 命中率 >= min-rate,
               且除"回合首请求"外无全 miss 步骤

退出码: 0=通过(可同步生产), 1=未通过(禁止同步), 2=参数/运行错误
"""
import argparse, json, os, re, sys, zstandard as zstd

VIOLATION_MARKERS = [
    (re.compile(r'\[' + r'i0\.|i\d\.\d{2}'), '注入行含易变重要度浮点 [i0.xx]'),
    (re.compile(r'/[\d]{4}-[\d]{2}-[\d]{2}/'), '注入行含易变日期'),
    (re.compile(r'topic:[^\]]'), '注入行含 topic 元数据(topic:0 噪声)'),
]
FREEZE_RE = re.compile(r'userCounts?|userCountBySession')
SORT_RE = re.compile(r'sort\(function.*(a\.id|b\.id|localeCompare)', re.S)


def check_plugin_static(plugin_path):
    problems = []
    try:
        text = open(plugin_path, encoding='utf-8').read()
    except OSError as e:
        return [f'插件无法读取: {e}']
    for pat, why in VIOLATION_MARKERS:
        if pat.search(text):
            problems.append(f'静态违规: {why}')
    if not FREEZE_RE.search(text):
        problems.append('静态违规: 未找到"回合内冻结"(userCount 刷新守卫)')
    if not SORT_RE.search(text):
        problems.append('静态违规: 未找到稳定排序(sort by id)')
    return problems


def parse_usage(text):
    uses = []
    for line in text.split('\n'):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get('type') != 'assistant/message':
            continue
        d = e.get('data', {})
        def find_u(o):
            if isinstance(o, dict):
                if 'cacheReadTokens' in o and 'inputTokens' in o:
                    return o
                for v in o.values():
                    r = find_u(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = find_u(v)
                    if r:
                        return r
            return None
        u = find_u(d)
        if u:
            uses.append(u)
    return uses


def session_files(root):
    out = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith('.zstd') and f.startswith('session'):
                out.append(os.path.join(base, f))
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def check_dynamic(sessions_root, recent_n, min_rate, plugin_mtime=0):
    reports = []
    all_ok = True
    for f in session_files(sessions_root)[:3]:
        sid = os.path.basename(os.path.dirname(f))[:22]
        stale = plugin_mtime > 0 and os.path.getmtime(f) < plugin_mtime
        try:
            with open(f, 'rb') as fh:
                text = zstd.ZstdDecompressor().stream_reader(fh).read().decode('utf-8', 'ignore')
        except Exception as e:
            reports.append({'session': sid, 'error': f'解压失败: {e}'})
            all_ok = False
            continue
        uses = parse_usage_with_turn(text)[-recent_n:]
        if not uses:
            reports.append({'session': sid, 'error': '无 usage 数据'})
            all_ok = False
            continue
        # 按"真实用户消息计数"分组（turn 字段与 user 消息有偏移，不准确）：
        # 每组（一个用户消息起的回合）允许 **1 个 miss**（刷新点=新注入生效的请求）；
        # 同组内第 2+ 个 miss => 真击穿（回合内前缀被改）
        by_group = {}
        for u in uses:
            by_group.setdefault(u['ugroup'], []).append(u)
        bad_steps = []
        for gname, steps in by_group.items():
            misses = 0
            for u in steps:
                cache = u.get('cacheReadTokens', 0)
                inc = u.get('inputTokens', 0)
                rate = cache / (cache + inc) if (cache + inc) else 1.0
                if rate < min_rate and inc > 1000:
                    misses += 1
                    if misses > 1:            # 每组第 2 个 miss = 击穿
                        bad_steps.append(round(rate, 3))
        ok = not bad_steps or stale
        all_ok = all_ok and ok
        tin = sum(u.get('inputTokens', 0) for u in uses)
        tch = sum(u.get('cacheReadTokens', 0) for u in uses)
        reports.append({
            'session': sid, 'n': len(uses), 'input': tin, 'cache': tch,
            'rate': round(tch / (tch + tin) * 100, 1) if (tch + tin) else 100.0,
            'bad_steps': bad_steps, 'ok': ok, 'stale': stale, 'error': None,
        })
    return reports, all_ok


def parse_usage_with_turn(text):
    uses = []
    ugroup = 0
    for line in text.split('\n'):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        t = e.get('type')
        if t == 'user/message':
            ugroup += 1          # 每个用户消息 → 新回合组
            continue
        if t != 'assistant/message':
            continue
        d = e.get('data', {})
        def find_u(o):
            if isinstance(o, dict):
                if 'cacheReadTokens' in o and 'inputTokens' in o:
                    return o
                for v in o.values():
                    r = find_u(v)
                    if r:
                        return r
            elif isinstance(o, list):
                for v in o:
                    r = find_u(v)
                    if r:
                        return r
            return None
        u = find_u(d)
        if u:
            u['turn'] = d.get('turn', '?')
            u['ugroup'] = ugroup
            uses.append(u)
    return uses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--plugin', default='/www/dsh/home/.agent-presets/_memory-plugin/plugin-v3.js')
    ap.add_argument('--sessions', default='/www/dsh/home/sessions')
    ap.add_argument('--min-rate', type=float, default=0.90)
    ap.add_argument('--recent', type=int, default=15)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    static = check_plugin_static(args.plugin)
    plugin_mtime = 0
    try:
        plugin_mtime = os.path.getmtime(args.plugin)
    except OSError:
        pass
    dyn, dyn_ok = check_dynamic(args.sessions, args.recent, args.min_rate, plugin_mtime)
    ok = (not static) and dyn_ok

    if args.json:
        print(json.dumps({'static_problems': static, 'dynamic': dyn, 'pass': ok}, ensure_ascii=False))
    else:
        print('=== 静态健康 ===')
        print('  ' + ('PASS ✓ 注入文本稳定' if not static else '\n  FAIL:\n    ' + '\n    '.join(static)))
        print('=== 动态健康(最近 %d 步) ===' % args.recent)
        for r in dyn:
            if r.get('error'):
                print(f"  {r['session']}: ERROR {r['error']}")
            elif r.get('stale'):
                print(f"  {r['session']}: 修复前历史(会话未再活跃) 命中率 {r['rate']}% — 仅参考")
            else:
                print(f"  {r['session']}: 命中率 {r['rate']}% (n={r['n']}) "
                      f"bad_steps={r['bad_steps']} {'✓' if r['ok'] else '✗'}")
        print('=== 结论: ' + ('PASS → 可同步生产' if ok else 'FAIL → 禁止同步, 先修') + ' ===')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
