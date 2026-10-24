#!/usr/bin/env python3
"""Offline analyser for DSH session logs (v3/v4 jsonl.zstd).

Purpose: measure, on REAL logs, (a) how the current deepmemory [历史脉络摘要]
chain is built from DSH compaction checkpoints and why it repeats, (b) whether
an incremental block compressor (billion-context style) can partition the same
history without overlaps, (c) how many bytes a reversible handle payload and an
incremental injection would take.

READ-ONLY: never writes into the sessions directory. The only writes are the
optional sqlite experiment DB and JSON report under --out.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import zstandard

CJK = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')


def est_tokens(text):
    if not text:
        return 0
    cjk = len(CJK.findall(text))
    return cjk + (len(text) - cjk) // 4 + (1 if (len(text) - cjk) % 4 else 0)


def ev_text(ev):
    """Extract human-readable text from one session event (message-producing)."""
    t = ev.get('type')
    d = ev.get('data') or {}
    parts = []
    if t == 'tool/result':
        blocks = ((d.get('message') or {}).get('content')) or []
        for b in blocks:
            if isinstance(b, dict) and b.get('type') == 'text':
                parts.append(b.get('text') or '')
        return '\n'.join(parts), 'tool/result'
    if t in ('user/message', 'assistant/message', 'system/message', 'developer/message'):
        m = d.get('message') or d
        c = m.get('content')
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get('type') == 'text':
                    parts.append(b.get('text') or '')
        src = m.get('source') or {}
        kind = src.get('kind') if isinstance(src, dict) else None
        return '\n'.join(parts), (t + (':' + kind if kind else ''))
    if t == 'tool/call':
        return str(d.get('arguments') or ''), 'tool/call:' + str(d.get('name'))
    return '', t or '?'


def read_events(path):
    raw = open(path, 'rb').read()
    dec = zstandard.ZstdDecompressor().stream_reader(
        __import__('io').BytesIO(raw), read_across_frames=True)
    data = dec.read()
    for line in data.split(b'\n'):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load(path):
    events, header = [], {}
    for e in read_events(path):
        if e.get('type') == 'session' and not header:
            header = e
            continue
        events.append(e)
    return header, events


def shingles(text, n=8):
    words = re.findall(r'\w+', text.lower())
    if len(words) < n:
        return set([' '.join(words)]) if words else set()
    return set(' '.join(words[i:i + n]) for i in range(len(words) - n + 1))


def overlap(a, b, n=8):
    sa, sb = shingles(a, n), shingles(b, n)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def common_prefix(a, b):
    m = min(len(a), len(b))
    i = 0
    while i < m and a[i] == b[i]:
        i += 1
    return i


def extract_proxy_digest(payload_events, budget=900):
    """Deterministic extractive stand-in for an LLM block summary.

    NOT a quality claim: keeps user intent, tool names/results headlines and
    error lines so we can measure volume / overlap structurally.
    """
    lines = []
    for ev, text, kind in payload_events:
        text = (text or '').strip()
        if not text:
            continue
        if kind.startswith('tool/result'):
            first = text.split('\n', 1)[0][:120]
            err = next((l for l in text.split('\n') if 'error' in l.lower() or 'Error' in l), '')
            lines.append('- [tool result] ' + first + (' | ' + err[:100] if err else ''))
        elif kind.startswith('tool/call'):
            lines.append('- [tool call] ' + kind.split(':', 1)[-1])
        elif kind.startswith('user/message'):
            lines.append('- [user] ' + ' '.join(text.split())[:220])
        elif kind.startswith('assistant/message'):
            lines.append('- [assistant] ' + ' '.join(text.split())[:160])
    out, used = [], 0
    for l in lines:
        if used + len(l) > budget:
            out.append('…')
            break
        out.append(l)
        used += len(l) + 1
    return '\n'.join(out)


def analyse(path):
    header, events = load(path)
    # seq -> event
    byseq = {}
    for e in events:
        if isinstance(e.get('seq'), int):
            byseq[e['seq']] = e
    summaries = [e for e in events if e.get('type') == 'compaction/summary']
    prunes = [e for e in events if e.get('type') == 'compaction/prune']

    # checkpoint node seqs: message events with source.kind == compact-checkpoint
    checkpoint_seqs = set()
    for seq, e in byseq.items():
        d = e.get('data') or {}
        m = d.get('message') or d
        src = m.get('source') if isinstance(m, dict) else None
        if isinstance(src, dict) and (
                src.get('kind') == 'compact-checkpoint'
                or (src.get('kind') == 'plugin' and src.get('plugin') == 'compact')):
            checkpoint_seqs.add(seq)

    covered_raw = set()          # raw (non-checkpoint) seqs already folded
    covered_any = set()
    coverage_chain = []
    blocks = []
    for i, s in enumerate(summaries):
        d = s.get('data') or {}
        seqs = d.get('shadowedSeqs') or []
        summ = d.get('summary')
        if isinstance(summ, list):
            stext = ''.join(b.get('text', '') for b in summ if isinstance(b, dict))
        else:
            stext = str(summ or '')
        raw_seqs = [q for q in seqs if q not in checkpoint_seqs]
        ckpt_seqs = [q for q in seqs if q in checkpoint_seqs]
        new_raw = [q for q in raw_seqs if q not in covered_raw]
        refold_raw = [q for q in raw_seqs if q in covered_raw]
        payload = []
        chars = tok = 0
        for q in new_raw:
            ev = byseq.get(q)
            if not ev:
                continue
            txt, kind = ev_text(ev)
            if txt:
                payload.append((ev, txt, kind))
                chars += len(txt)
                tok += est_tokens(txt)
        ckpt_chars = 0
        for q in ckpt_seqs:
            ev = byseq.get(q)
            if ev:
                ckpt_chars += len(ev_text(ev)[0])
        block = {
            'index': i + 1,
            'seq': s.get('seq'),
            'range': d.get('shadowedRange'),
            'shadowed_n': len(seqs),
            'shadowed_tokens': d.get('shadowedTokenCount') or 0,
            'checkpoint_only': len(raw_seqs) == 0,
            'checkpoint_n': len(ckpt_seqs),
            'checkpoint_chars': ckpt_chars,
            'new_raw_n': len(new_raw),
            'new_seqs': new_raw,
            'refold_raw_n': len(refold_raw),
            'new_raw_chars': chars,
            'new_raw_tokens_est': tok,
            'summary_chars': len(stext),
            'summary_head': ' '.join(stext.split())[:120],
            'payload': payload,
            'summary': stext,
        }
        blocks.append(block)
        covered_raw.update(new_raw)
        covered_any.update(seqs)
        prev = set(coverage_chain[-1]['covered']) if coverage_chain else set()
        coverage_chain.append({
            'index': i + 1,
            'covered': set(covered_raw),
            'new_raw_n': len(new_raw),
            'represented_overlap_with_prev': round(
                len(prev & covered_raw) / max(1, len(covered_raw)), 4),
        })

    # ---- duplication of the CURRENT injection (last 5 x 240 chars) ----
    inj_blocks = []
    for b in blocks[-5:]:
        inj_blocks.append(('[摘要#%s] ' % (b['seq'],)) + str(b['summary'])[:240])
    inj_text = '[历史脉络摘要]\n' + '\n'.join(inj_blocks) + '\n'
    stripped = [re.sub(r'^\[摘要#[^\]]*\]\s*', '', b) for b in inj_blocks]
    pair = []
    for a in range(len(stripped)):
        for c in range(a + 1, len(stripped)):
            pair.append({
                'a': a, 'b': c,
                'common_prefix_stripped': common_prefix(stripped[a], stripped[c]),
                'first_line_equal': stripped[a].split('\n', 1)[0] == stripped[c].split('\n', 1)[0],
                'shingle_overlap': round(overlap(stripped[a], stripped[c]), 3),
            })
    full_pair = []
    last5 = blocks[-5:]
    for a in range(len(last5)):
        for c in range(a + 1, len(last5)):
            full_pair.append({
                'a': a, 'b': c,
                'full_shingle_overlap': round(overlap(last5[a]['summary'], last5[c]['summary']), 3),
                'full_common_prefix': common_prefix(last5[a]['summary'], last5[c]['summary']),
            })
    # unique char coverage of current injection, char-40 shingles
    def char_shingles(s, n=40):
        return set(s[i:i + n] for i in range(0, max(0, len(s) - n + 1)))
    sh_all = char_shingles('\n'.join(inj_blocks))
    uni = set()
    for blk in inj_blocks:
        uni |= char_shingles(blk)
    dup_ratio = 0.0 if not sh_all else round(1 - len(uni) / len(sh_all), 3)

    # ---- proposed incremental injection: one digest per block, budget-aware ----
    digests = []
    for b in blocks:
        dg = extract_proxy_digest(b['payload'])
        digests.append({'seq': b['seq'], 'digest': dg, 'chars': len(dg)})
    # incremental injection = all digests, capped at INJECT budget (2600 chars)
    budget = 2600
    parts, used, kept = [], 0, 0
    for dg in reversed(digests):          # newest first wins budget
        if used + len(dg['digest']) > budget:
            continue
        parts.append(dg['digest'])
        used += len(dg['digest'])
        kept += 1
    inc_text = '[历史脉络摘要·增量块]\n' + '\n'.join(reversed(parts)) + '\n'
    # duplication of proposed injection (should be ~0: disjoint sources)
    inc_blocks = list(reversed(parts))
    inc_uni = set()
    for blk in inc_blocks:
        inc_uni |= char_shingles(blk)
    inc_all = char_shingles('\n'.join(inc_blocks))
    inc_dup = 0.0 if not inc_all else round(1 - len(inc_uni) / len(inc_all), 3)

    # compact scenario: every block gets a 250-char deterministic digest
    cparts = []
    for b in blocks:
        dg = extract_proxy_digest(b['payload'], budget=250)
        if dg:
            cparts.append(dg)
    compact_text = '[历史脉络摘要·增量块]\n' + '\n'.join(cparts) + '\n'
    cuni = set()
    for blk in cparts:
        cuni |= char_shingles(blk)
    call = char_shingles('\n'.join(cparts))
    compact_dup = 0.0 if not call else round(1 - len(cuni) / len(call), 3)
    compact_injection = {
        'text': compact_text, 'chars': len(compact_text),
        'tokens_est': est_tokens(compact_text), 'blocks_kept': len(cparts),
        'char40_dup_ratio': compact_dup,
    }

    # ---- cache-stability: bytes before the fold point vs folded+tail ----
    msg_like = ('user/message', 'assistant/message', 'system/message',
                'tool/result', 'tool/call', 'developer/message')
    tok_by_seq = {}
    for seq, e in byseq.items():
        if e.get('type') in msg_like:
            txt, _ = ev_text(e)
            if txt:
                tok_by_seq[seq] = est_tokens(txt)
    visible_prefix_tokens = 0
    cache_rows = []
    folded_so_far = set()
    for b in blocks:
        rng = b['range'] or {}
        start = rng.get('start', 0)
        prefix = sum(t for seq, t in tok_by_seq.items()
                     if seq < start and seq not in folded_so_far)
        total = prefix + (b['shadowed_tokens'] or 0)
        cache_rows.append({
            'index': b['index'],
            'prefix_tokens_est': prefix,
            'folded_tokens': b['shadowed_tokens'],
            'stable_fraction': round(prefix / total, 4) if total else None,
        })
        folded_so_far.update((b['range'] or {}).get('start', 0) and range(
            (b['range'] or {}).get('start', 0), (b['range'] or {}).get('end', 0) + 1) or [])

    # ---- reversible store ----
    sqlite_path = os.path.join(os.path.dirname(path), '..', '..', 'blocks.db')
    return header, events, blocks, prunes, {
        'session_id': header.get('id'),
        'log': os.path.abspath(path),
        'log_bytes': os.path.getsize(path),
        'event_count': len(events),
        'n_compactions': len(blocks),
        'n_prunes': len(prunes),
        'n_checkpoint_nodes': len(checkpoint_seqs),
        'blocks': [{k: (v if k not in ('payload', 'summary') else None) for k, v in b.items()}
                   for b in blocks],
        'totals': {
            'raw_events_folded_once': len(covered_raw),
            'raw_events_refolded': sum(b['refold_raw_n'] for b in blocks),
            'checkpoint_only_compactions': sum(1 for b in blocks if b['checkpoint_only']),
            'raw_chars_folded': sum(b['new_raw_chars'] for b in blocks),
            'raw_tokens_est_folded': sum(b['new_raw_tokens_est'] for b in blocks),
            'summary_chars_total': sum(b['summary_chars'] for b in blocks),
            'fold_ratio_chars': round(
                sum(b['new_raw_chars'] for b in blocks) / max(1, sum(b['summary_chars'] for b in blocks)), 2),
            'reconsolidated_chars': sum(b.get('checkpoint_chars', 0) for b in blocks),
            'reconsolidated_ratio': round(
                sum(b.get('checkpoint_chars', 0) for b in blocks) /
                max(1, sum(b['summary_chars'] for b in blocks)), 3),
            'fold_ratio_tokens': round(
                sum(b['new_raw_tokens_est'] for b in blocks) /
                max(1, sum(est_tokens(b['summary']) for b in blocks)), 2),
        },
        'current_injection': {
            'text': inj_text,
            'chars': len(inj_text),
            'tokens_est': est_tokens(inj_text),
            'pairwise': pair,
            'full_pairwise': full_pair,
            'char40_dup_ratio': dup_ratio,
        },
        'compact_injection': compact_injection,
        'proposed_injection': {
            'text': inc_text,
            'chars': len(inc_text),
            'tokens_est': est_tokens(inc_text),
            'blocks_kept': kept,
            'char40_dup_ratio': inc_dup,
        },
        'source_coverage': [
            {'index': c['index'], 'covered_n': len(c['covered']), 'new_raw_n': c['new_raw_n'],
             'represented_overlap_with_prev': c['represented_overlap_with_prev']}
            for c in coverage_chain],
        'cache_stability': cache_rows,
    }, sqlite_path


def reversible_test(blocks, sqlite_path):
    os.makedirs(os.path.dirname(os.path.abspath(sqlite_path)), exist_ok=True)
    if os.path.exists(sqlite_path):
        os.remove(sqlite_path)
    con = sqlite3.connect(sqlite_path)
    con.execute('''CREATE TABLE blocks(
        handle TEXT PRIMARY KEY, session_id TEXT, start_seq INT, end_seq INT,
        engine TEXT, created_at REAL, payload BLOB, sha256 TEXT, raw_chars INT, raw_tokens_est INT)''')
    rows = []
    total_raw = total_z = 0
    for b in blocks:
        if not b['payload']:
            continue
        payload = json.dumps([{'seq': ev.get('seq'), 'type': ev.get('type'),
                               'kind': kind, 'text': text}
                              for ev, text, kind in b['payload']],
                             ensure_ascii=False)
        raw = payload.encode('utf-8')
        z = zstandard.ZstdCompressor(level=6).compress(raw)
        handle = 'bh_%s' % hashlib.sha1(raw).hexdigest()[:16]
        sha = hashlib.sha256(raw).hexdigest()
        con.execute('INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (handle, 'sim', b['range']['start'], b['range']['end'], 'bc-prototype',
                     0.0, z, sha, len(payload), b['new_raw_tokens_est']))
        rows.append({'handle': handle, 'sha256': sha, 'raw_bytes': len(raw), 'zstd_bytes': len(z),
                     'start': b['range']['start'], 'end': b['range']['end'],
                     'events': len(b['payload'])})
        total_raw += len(raw)
        total_z += len(z)
    con.commit()
    # expand every block from sqlite only, verify bytes
    ok = 0
    for r in rows:
        z = con.execute('SELECT payload FROM blocks WHERE handle=?', (r['handle'],)).fetchone()[0]
        raw = zstandard.ZstdDecompressor().decompress(z)
        if hashlib.sha256(raw).hexdigest() == r['sha256']:
            ok += 1
    con.close()
    return {'db': sqlite_path, 'blocks_stored': len(rows), 'expand_verified': ok,
            'raw_bytes': total_raw, 'zstd_bytes': total_z,
            'compression_ratio': round(total_raw / max(1, total_z), 2), 'rows': rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--out', default='/tmp/bc-exp/report.json')
    ap.add_argument('--dump-injection', action='store_true')
    ap.add_argument('--export-blocks', default='')
    a = ap.parse_args()
    report = {'sessions': [], 'reversible': None}
    all_blocks = []
    for p in a.logs:
        header, events, blocks, prunes, rep, sp = analyse(p)
        report['sessions'].append(rep)
        all_blocks.extend(blocks)
        if a.dump_injection:
            print('=' * 30, os.path.basename(p))
            print('--- current injection ---')
            print(rep['current_injection']['text'])
            print('--- proposed incremental injection ---')
            print(rep['proposed_injection']['text'])
    report['reversible'] = reversible_test(all_blocks, '/tmp/bc-exp/blocks.db')
    if a.export_blocks:
        with open(a.export_blocks, 'w', encoding='utf-8') as f:
            for p, rep in zip(a.logs, report['sessions']):
                header, events, blocks, prunes, _, _ = analyse(p)
                for b in blocks:
                    payload_text = '\n\n'.join(
                        '[%s seq=%s] %s' % (k, ev.get('seq'), t)
                        for ev, t, k in b['payload'])
                    rec = {
                        'session_id': rep['session_id'], 'index': b['index'],
                        'seq': b['seq'], 'range': b['range'],
                        'checkpoint_n': b['checkpoint_n'],
                        'new_raw_n': b['new_raw_n'],
                        'new_seqs': b['new_seqs'],
                        'payload_text': payload_text,
                        'payload_events': [
                            {'seq': ev.get('seq'), 'type': ev.get('type'), 'kind': k, 'text': t}
                            for ev, t, k in b['payload']],
                        'payload_tokens_est': b['new_raw_tokens_est'],
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print('report:', a.out)


if __name__ == '__main__':
    main()
