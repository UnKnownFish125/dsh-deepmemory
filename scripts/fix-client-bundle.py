#!/opt/AstrBot/venv/bin/python3
"""把 agent 写的 ESM client.js 转换为 dsh __ModuleLoader__.load 协议格式。
幂等：已包裹则只清理 import/export，不重复包裹。
自动保证 React 绑定存在（vite 写回可能吃掉 require 行，这里兜底补齐）。
用法: fix-client-bundle.py <client.js路径> <插件id>"""
import re, subprocess, sys

HEAD = "__ModuleLoader__.load({\n  id: '%s',\n  factory: (require) => {\n"
TAIL = "\n    return { name, apply }\n  }\n})\n"

def ensure_react_binding(src):
    """保证 factory 作用域内有 React 绑定。"""
    if "const React = require('react')" in src:
        return src
    if re.search(r"\bReact\.", src) is None:
        return src
    anchor = "factory: (require) => {\n"
    if anchor in src:
        return src.replace(anchor, anchor + "const React = require('react')\n", 1)
    # 未包裹文件顶部补 import
    return "import * as React from 'react'\n" + src

def main():
    path, pid = sys.argv[1], sys.argv[2]
    src = open(path).read()
    # 1) import -> require
    src = re.sub(r"^import \* as (\w+) from '([^']+)'", r"const \1 = require('\2')", src, flags=re.M)
    src = re.sub(r"^import (\w+) from '([^']+)'", r"const \1 = require('\2')", src, flags=re.M)
    src = re.sub(r"^import \{([^}]+)\} from '([^']+)'", r"const {\1} = require('\2')", src, flags=re.M)
    # 2) 去 export
    src = re.sub(r"^export default ", "", src, flags=re.M)
    src = re.sub(r"^export function ", "function ", src, flags=re.M)
    src = re.sub(r"^export const ", "const ", src, flags=re.M)
    src = re.sub(r"^export \{", "{", src, flags=re.M)
    src = src.rstrip()

    # 3) 包裹（幂等）
    already = src.startswith("__ModuleLoader__.load({")
    if already:
        src, replacements = re.subn(
            r"(__ModuleLoader__\.load\(\{\s*id:\s*)'[^']+'",
            lambda match: match.group(1) + repr(pid),
            src,
            count=1,
        )
        if replacements != 1:
            print("ERROR: 已包裹文件缺少 loader id")
            sys.exit(1)
        src = ensure_react_binding(src)
        open(path, 'w').write(src + "\n")
        print("已包裹，跳过包裹（仅清理 import/export）")
    else:
        has_name = bool(re.search(r"(?:const|let|var) name\s*=", src))
        ret = "return { name, apply }" if has_name else "return { name: '%s', apply }" % pid
        src = ensure_react_binding(src)
        wrapped = (HEAD % pid) + src + "\n" + ret + "\n  }\n})\n"
        open(path, 'w').write(wrapped)
        print("已包裹 __ModuleLoader__.load")

    # 4) 验证：层数 + 语法 + 模拟（含真实渲染路径）
    final = open(path).read()
    layers = final.count('__ModuleLoader__.load({')
    if layers != 1:
        print("ERROR: 包裹层数 %d != 1，需要人工处理" % layers)
        sys.exit(1)
    if "require('react')" not in final:
        print("ERROR: React 绑定缺失")
        sys.exit(1)
    loader_id = re.search(r"__ModuleLoader__\.load\(\{\s*id:\s*'([^']+)'", final)
    if not loader_id or loader_id.group(1) != pid:
        print("ERROR: loader id 不匹配: %s != %s" % (
            loader_id.group(1) if loader_id else "<missing>", pid
        ))
        sys.exit(1)
    r = subprocess.run(['node', '--check', path], capture_output=True, text=True)
    if r.returncode != 0:
        print("语法错误:", r.stderr[:400]); sys.exit(1)
    sim = (
        "global.__ModuleLoader__={load:(h)=>{global.__handoff=h;}};"
        "let renderFn=null;"
        "global.document={createElement:()=>({dataset:{},style:{},appendChild(){},remove(){}}),head:{appendChild(){}}};"
        "const React={createElement:()=>({})};"
        "const code=require('fs').readFileSync('%s','utf8');"
        "new Function('require',code)((m)=>{if(m==='react')return React;throw new Error('x:'+m);});"
        "const h=global.__handoff;"
        "const e=h.factory((m)=>{if(m==='react')return React;throw new Error('x:'+m);});"
        "const fakeCtx={get:(k)=>(k==='slots'?{inject:(kk,cb)=>{cb();},register:(c,fn)=>{renderFn=fn;return()=>{};}}:undefined),effect:()=>()=>{}};"
        "e.apply(fakeCtx);"
        "if(typeof renderFn!=='function')throw new Error('renderFn 未注册');"
        "const out=renderFn({sessionId:'sim'});"
        "console.log('registered id:',h.id);"
        "console.log('name:',e.name,'| apply:',typeof e.apply,'| render:',!!out);"
    ) % path
    r2 = subprocess.run(['node', '-e', sim], capture_output=True, text=True)
    if r2.returncode != 0:
        print("模拟失败:", r2.stderr[:400]); sys.exit(1)
    print("全部验证通过:", r2.stdout.strip())

if __name__ == "__main__":
    main()
