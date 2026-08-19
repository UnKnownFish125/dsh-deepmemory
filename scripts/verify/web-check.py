#!/opt/AstrBot/venv/bin/python3
"""浏览器级验证（空白试验机第四层）：真无头 Chromium 打开临时 dsh web 实例，
走完整用户路径：欢迎页 -> 新建会话 -> 对话页 -> 点击「记忆」tab -> 面板渲染，
并断言无致命 console 错误。

环境变量:
  VERIFY_BASE_URL  临时 dsh web 实例地址 (http://localhost:PORT)
  VERIFY_SHOT      可选，最终截图输出路径
"""
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("VERIFY_BASE_URL", "")
if not BASE:
    print("VERIFY_BASE_URL 未设置"); sys.exit(1)

errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. 页面加载（vite 首编译后 bundle 就绪）
    page.goto(BASE, wait_until="networkidle", timeout=120000)
    print("1. 页面加载 OK:", page.title()[:60])

    # 2. 欢迎页/测试通知 -> Continue
    cont = page.get_by_text("Continue", exact=True)
    if cont.count() > 0:
        cont.first.click()
        page.wait_for_timeout(2000)

    # 2b. 临时 home 无 credentials，跳过 API key 配置弹窗（若有）
    later = page.get_by_text("Configure later", exact=True)
    if later.count() > 0:
        later.first.click()
        page.wait_for_timeout(2000)

    # 3. 新建会话（workspace.json 已带入，注册表有工作区）
    new_btn = page.get_by_text("New Session", exact=True)
    new_btn.first.wait_for(state="visible", timeout=30000)
    # 遮罩兜底：Escape 关闭任何残留 modal
    try:
        new_btn.first.click(timeout=8000)
    except Exception:
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        new_btn.first.click()
    page.wait_for_timeout(6000)

    # 3b. blank 会话隐藏 header/tab 条：发送一条消息解除（首条消息后 tab 环出现）
    box = page.locator('textarea, [contenteditable="true"], [role="textbox"]').first
    if box.count() > 0:
        box.click()
        page.wait_for_timeout(800)
        box.type("hi")
        page.wait_for_timeout(800)
        box.press("Enter")
        # 等待消息进入会话、header 解除隐藏（不需要等 agent 回复完成）
        page.wait_for_timeout(8000)

    # 4. 对话页出现后点击「记忆」tab
    tab = page.get_by_text("记忆", exact=True).first
    tab.wait_for(state="visible", timeout=30000)
    tab.click()

    # 5. 记忆面板渲染出内容
    panel = page.locator(".dsh-mem-panel:visible").first
    panel.wait_for(state="visible", timeout=30000)
    text = panel.inner_text()
    if len(text) < 20:
        raise AssertionError("面板内容过短: " + repr(text[:80]))
    print("3. 记忆面板渲染 OK,", len(text), "字符; 首行:", text.splitlines()[0][:60])

    # 普通记忆必须保留内容编辑入口，并走通真实 PUT 保存链路。
    fixture = "web-check-memory-edit-fixture"
    edited_fixture = fixture + "-updated"
    added = page.evaluate("""async ({fixture}) => {
      const r = await fetch('/mem-api/v1/memories/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({content:fixture, type:'fact', scope:'workspace', domain:'work', workspace_id:'deepseek-hardness'})});
      return await r.json();
    }""", {"fixture": fixture})
    fixture_id = added.get("id")
    try:
        panel.locator("button:visible").filter(has_text="刷新").first.click()
        fixture_text = panel.get_by_text(fixture, exact=True)
        fixture_text.wait_for(state="visible", timeout=10000)
        memory_row = fixture_text.locator("xpath=../..")
        edit_button = memory_row.locator(".dsh-mem-memory-edit")
        if edit_button.count() != 1:
            raise AssertionError("3. 普通记忆编辑按钮缺失")
        edit_button.click()
        editor = memory_row.locator(".dsh-mem-memory-editor")
        editor.fill(edited_fixture)
        panel.locator(".dsh-mem-memory-edit-actions button").filter(has_text="保存").click()
        panel.get_by_text(edited_fixture, exact=True).wait_for(state="visible", timeout=10000)
        print("3. 普通记忆编辑保存链路 OK")
    finally:
        if fixture_id:
            page.evaluate("async ({id}) => { await fetch('/mem-api/v1/memories/' + id, {method:'DELETE'}); }", {"id": fixture_id})

    # 4. 图谱视图：Obsidian 交互（节点拖拽 + hover 高亮 + 重置）
    page.get_by_text("图谱", exact=True).first.click()
    svg = page.locator(".dsh-mem-graph-svg").first
    svg.wait_for(state="visible", timeout=20000)
    circles = page.locator(".dsh-mem-graph-node")
    if circles.count() > 0:
        c1 = circles.first.bounding_box()
        cx = c1["x"] + c1["width"] / 2
        cy = c1["y"] + c1["height"] / 2
        # React 合成事件用原生 dispatch 更可靠（playwright mouse API 有时不触发 React handler）
        circles.first.dispatch_event("mousedown", {"bubbles": True, "clientX": cx, "clientY": cy, "button": 0})
        for i in range(1, 11):
            svg.dispatch_event("mousemove", {"bubbles": True, "clientX": cx + i * 7, "clientY": cy + i * 4.5})
        svg.dispatch_event("mouseup", {"bubbles": True, "clientX": cx + 70, "clientY": cy + 45})
        page.wait_for_timeout(600)
        c2 = circles.first.bounding_box()
        moved = abs(c2["x"] - c1["x"]) + abs(c2["y"] - c1["y"])
        if moved < 8:
            raise AssertionError("图谱节点拖拽无效: 位移 " + str(moved))
        print("4. 图谱节点拖拽 OK (位移", round(moved, 1), "px)")
    else:
        print("4. 图谱暂无节点（跳过拖拽断言）")

    # 5. 设置 → 插件 → 插件配置：deepmemory 官方风卡片折叠/展开
    page.get_by_text("Settings", exact=True).first.click()
    page.wait_for_timeout(2500)
    page.get_by_text("Plugins", exact=True).first.click()
    page.wait_for_timeout(2500)
    card_header = page.get_by_text("deepmemory", exact=True).first
    if card_header.count() == 0:
        raise AssertionError("插件配置页未找到 deepmemory 卡片")
    body_txt = page.inner_text("body")
    if "基础连接" in body_txt:
        raise AssertionError("卡片默认应折叠，但配置组已展开")
    # 点击 header 展开
    card_header.click()
    page.wait_for_timeout(1200)
    if "基础连接" not in page.inner_text("body"):
        raise AssertionError("点击卡片头后配置组未出现")
    # 再点收起
    card_header.click()
    page.wait_for_timeout(1200)
    if "基础连接" in page.inner_text("body"):
        raise AssertionError("再次点击后配置组仍显示")
    print("5. 插件配置卡折叠/展开 OK:", page.get_by_text("deepmemory 配置", exact=False).first.inner_text()[:40])

    # 6. AST-25 P1: 任务看板功能验证
    # 先关闭 Settings 返回记忆面板
    page.keyboard.press("Escape")
    page.wait_for_timeout(1500)
    memory_tab = page.get_by_text("记忆", exact=True)
    if memory_tab.count() > 0:
        memory_tab.first.click()
        page.wait_for_timeout(700)
    panel = page.locator(".dsh-mem-panel:visible").first
    panel_back = panel.locator("button:visible").filter(has_text="返回")
    if panel_back.count() == 0:
        panel_back = panel.locator("button:visible").filter(has_text="Back")
    if panel_back.count() > 0:
        panel_back.first.click()
        page.wait_for_timeout(700)
    # 检查任务看板按钮存在（验证功能已添加）
    task_btn = panel.locator("button:visible").filter(has_text="任务看板")
    if task_btn.count() == 0:
        task_btn = panel.locator("button:visible").filter(has_text="Task Board")
    if task_btn.count() == 0:
        raise AssertionError("6. 任务看板按钮未找到")
    task_btn.first.click()
    page.wait_for_timeout(1000)
    panel = page.locator(".dsh-mem-panel:visible").first
    board = panel.locator(".dsh-mem-task-board").first
    board.wait_for(state="visible", timeout=10000)
    if board.locator(".dsh-mem-task-column").count() != 5:
        raise AssertionError("6. 任务看板不是固定五列")
    if board.locator(".dsh-mem-task-column[data-status='todo']").count() != 1 or board.locator(".dsh-mem-task-column[data-status='in_progress']").count() != 1:
        raise AssertionError("6. 待办与进行中没有独立列")
    if board.locator(".dsh-mem-task-priority").count() == 0 and board.locator(".dsh-mem-task-card").count() > 0:
        raise AssertionError("6. 任务卡缺少 B 版颜色识别条")
    print("6. B 版任务看板固定五列 OK")
    panel.locator("button:visible").filter(has_text="返回").first.click()
    page.wait_for_timeout(700)

    # 7. AST-25 P1: 会话配置验证（默认配置保存 + session override/reset 逻辑存在）
    # 注：完整测试需要真实会话 ID，这里验证 UI 元素存在即可
    page.get_by_text("Settings", exact=True).first.click()
    page.wait_for_timeout(2000)
    page.get_by_text("Plugins", exact=True).first.click()
    page.wait_for_timeout(2000)
    card_header = page.get_by_text("deepmemory", exact=True).first
    card_header.click()
    page.wait_for_timeout(1500)
    # 验证配置项可编辑并保存
    save_btn = page.get_by_text("保存全部", exact=True)
    if save_btn.count() == 0:
        save_btn = page.get_by_text("Save all", exact=True)
    if save_btn.count() == 0:
        raise AssertionError("7. 配置保存按钮未找到")
    print("7. 配置保存按钮 OK")

    # 8. AST-25 P1: 状态卡与敏感内容 UI 验证
    # 返回记忆面板检查状态卡显示
    page.keyboard.press("Escape")
    page.wait_for_timeout(1000)
    body_text = page.inner_text("body")
    # 状态卡应该显示（即使为空）
    if not any(k in body_text for k in ("工作区状态卡", "Workspace Card", "状态卡")):
        raise AssertionError("8. 状态卡标题未找到")
    print("8. 状态卡显示 OK")

    # 敏感内容 UI 验证：检查相关文案是否已加载（即使没有敏感记忆）
    # 通过检查 CSS 类是否存在来验证功能已实现
    has_sensitive_css = page.evaluate("() => document.querySelector('style[data-plugin=\"deepmemory\"]')?.textContent?.includes('dsh-mem-sensitive-box') || false")
    if not has_sensitive_css:
        raise AssertionError("9. 敏感内容 UI CSS 未加载")
    print("9. 敏感内容 UI CSS 已加载 OK")

    # 10. 无致命 console 错误
    fatal = [e for e in errors if any(k in e for k in (
        "is not defined", "ReferenceError", "Cannot read", "Failed to fetch", "Unexpected token"))]
    if fatal:
        raise AssertionError("致命 console 错误: " + " | ".join(fatal[:5]))

    shot = os.environ.get("VERIFY_SHOT")
    if shot:
        page.screenshot(path=shot, full_page=False)
    browser.close()
    print("web-check 全部通过（含 AST-25 P1 验证）")
