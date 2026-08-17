# Deepmemory v2 Preset Implementation Summary

## What Was Delivered

Implemented three independent preset configurations with full static verification as specified in AST-21.

### 📦 Preset Configurations

Created three preset directories under `agent-preset/`:

1. **`agent-preset/task/`** - Task work mode
   - Full coding capabilities: bash, filesystem, subagents, workflows, plan mode, goals, todos
   - Complete delegation: spawn/fork subagents, workflow engine, Ralph
   - Task management: task state cards, task boards, process memory
   - Budget profile: `task-default` with priorities for task_state_card (0.15), task_board (0.10), active_memory (0.15)
   - Soft target: 70%, hard limit: 85%
   - Process memory active: 15 days

2. **`agent-preset/daily/`** - Daily Q&A mode
   - Lightweight tools: bash, filesystem, web, ask-user
   - No delegation: excludes subagents, workflows, todos, Ralph
   - Daily state management: daily state cards, short-term memory, topic suspension
   - Budget profile: `daily-default` with priorities for daily_state_card (0.10), active_memory (0.20)
   - Soft target: 60%, hard limit: 75%
   - Daily memory active: 7 days, max 3 suspended topics (24h TTL)

3. **`agent-preset/blank-template/`** - Third-party plugin template (existing, enhanced)
   - Minimal capabilities: persona, agent instructions, plugin entry point
   - No deepmemory business logic
   - Updated budget documentation with example structure
   - Clean template for third-party developers

### 📋 Budget Configuration Structure

Each preset includes structured budget profiles:

```yaml
deepmemory:
  preset_mode: task|daily
  budget_profile: task-default|daily-default
  context:
    soft_target_ratio: 0.60-0.70  # Soft budget target
    hard_limit_ratio: 0.75-0.85   # Hard ceiling
    priority_allocation:
      component_name:
        ratio: 0.XX               # Target allocation
        priority: 1-5             # Lower = higher priority
        min_tokens: XXXX          # Minimum guarantee
```

Key principles:
- Ratios don't need to sum to 1.0 (unused budget returns to pool)
- Priority ordering determines allocation under pressure
- Soft targets allow expansion, hard limits prevent context overflow
- Each component has minimum token guarantees

### ✅ Static Verification

Created `scripts/verify/preset-check.py` to verify:

1. **File existence**: preset.yml and agent.cordis.yml for all three presets
2. **YAML validity**: Actual YAML parsing with PyYAML to catch malformed files
3. **Capability boundaries**:
   - Task: requires subagent, workflow, plan-mode, todo, delegation
   - Daily: requires memory, forbids subagent/workflow/todo/delegation
   - Blank: forbids harness-memory
4. **Memory plugin presence**: Task and daily must have it, blank must not
5. **Budget configuration**: Task and daily must have complete budget profiles with structured validation
6. **Budget field validation**:
   - soft_target_ratio and hard_limit_ratio must be between 0 and 1
   - soft_target_ratio must be <= hard_limit_ratio
   - priority must be positive integer and unique across components
   - ratio must be between 0 and 1
   - min_tokens must be non-negative integer
7. **Required budget fields**: Preset-specific fields verified (task_state_card, task_board, daily_state_card, etc.)

Updated `scripts/verify.sh` to run preset contract checks as step 3.1.

### 🧪 Verification Results

```
[verify] server.py 语法                          OK
[verify] 隔离实例冒烟 (临时data+随机端口)        写入✓ 检索✓ 图谱✓ 备份✓ 重建✓
[verify] client.js 转换+渲染验证                 OK
[verify] preset 插件语法+apply+defineTool       OK
[verify] preset 契约验证 (task/daily/blank)     OK
[verify] 浏览器模拟 (临时实例+无头Chromium)      OK

[verify] 全部通过 — 可以进入生产
```

All verification layers passed:
1. ✅ Memory server: syntax, isolated instance, API smoke tests
2. ✅ Web client: ESM transformation, rendering
3. ✅ Preset plugin: syntax, apply, defineTool format
4. ✅ Preset contracts: all three presets validated with actual YAML parsing and structured budget validation
5. ✅ Browser simulation: full integration test

## File Structure

```
agent-preset/
├── task/
│   ├── preset.yml                 # Task mode metadata
│   └── agent.cordis.yml           # Full capabilities + task budget
├── daily/
│   ├── preset.yml                 # Daily mode metadata
│   └── agent.cordis.yml           # Lightweight capabilities + daily budget
├── blank-template/
│   ├── preset.yml                 # Template metadata
│   ├── agent.cordis.yml           # Minimal skeleton + budget docs
│   ├── README.md                  # Usage instructions
│   └── plugin/
│       └── plugin.js              # Entry point placeholder
├── memory-plugin/
│   ├── plugin-v2.js               # (existing)
│   └── plugin-v3.js               # (existing, used by task/daily)
├── agent.cordis.yml               # (existing legacy entry)
└── preset.yml                     # (existing legacy entry)

scripts/verify/
├── plugin-check.mjs               # (existing)
├── render-check.mjs               # (existing)
├── web-check.py                   # (existing)
└── preset-check.py                # NEW: preset contract verification with actual YAML parsing
```

## Compatibility

- ✅ **Backward compatible**: Original `agent-preset/agent.cordis.yml` remains untouched
- ✅ **No breaking changes**: Existing deployments continue to work
- ✅ **Memory server unchanged**: No server API modifications
- ✅ **Web plugin unchanged**: No UI modifications
- ✅ **Install script unchanged**: `scripts/install.sh` untouched
- ✅ **Git clean**: Ready for new branch and commit

## What Was NOT Done (Per Requirements)

As specified in the issue DON'T section:
- ❌ No server API changes
- ❌ No deployment to production
- ❌ No service restarts
- ❌ No git tags
- ❌ No push to main
- ❌ No changes to production presets
- ❌ No guessing of Harness API semantics (reused existing verified structures)

## Next Steps

Ready to:
1. Create new branch from main@240268b
2. Commit these changes with verification evidence
3. Submit for review

No deployment, restart, or tag creation - contract implementation only.
