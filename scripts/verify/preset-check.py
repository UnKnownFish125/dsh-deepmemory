#!/usr/bin/env python3
"""
Preset contract verification: checks that task/daily/blank presets exist,
are valid YAML, have required fields, and meet capability/budget requirements.
"""

import sys
import yaml
from pathlib import Path

# Custom YAML loader that handles !!js tags (treats them as strings)
class CustomLoader(yaml.SafeLoader):
    pass

def js_constructor(loader, node):
    """Handle !!js tags by returning the value as a string"""
    return loader.construct_scalar(node)

CustomLoader.add_constructor('tag:yaml.org,2002:js', js_constructor)

ROOT = Path(__file__).parent.parent.parent

PRESETS = [
    {
        'name': 'task',
        'path': 'agent-preset/task',
        'required_capabilities': ['subagent', 'workflow', 'plan-mode', 'todo', 'delegation'],
        'required_budget_fields': ['task_state_card', 'task_board', 'active_memory'],
        'must_have_memory': True,
        'must_have_budget': True,
        'expected_preset_mode': 'task',
    },
    {
        'name': 'daily',
        'path': 'agent-preset/daily',
        'required_capabilities': ['memory'],
        'forbidden_capabilities': ['subagent', 'workflow', 'todo', 'delegation'],
        'required_budget_fields': ['daily_state_card', 'active_memory'],
        'must_have_memory': True,
        'must_have_budget': True,
        'expected_preset_mode': 'daily',
    },
    {
        'name': 'blank',
        'path': 'agent-preset/blank-template',
        'required_capabilities': [],
        'forbidden_capabilities': ['harness-memory'],
        'must_have_memory': False,
        'must_have_budget': False,
        'expected_preset_mode': None,
    },
]

failures = 0

def fail(msg):
    global failures
    print(f"  ✗ {msg}")
    failures += 1

def ok(msg):
    print(f"  ✓ {msg}")

def check_preset(spec):
    print(f"\n[{spec['name']} preset]")

    preset_yml_path = ROOT / spec['path'] / 'preset.yml'
    agent_yml_path = ROOT / spec['path'] / 'agent.cordis.yml'

    # Check files exist
    if not preset_yml_path.exists():
        fail(f"preset.yml not found at {spec['path']}")
        return
    if not agent_yml_path.exists():
        fail(f"agent.cordis.yml not found at {spec['path']}")
        return
    ok('preset.yml and agent.cordis.yml exist')

    # Parse preset.yml with actual YAML parser
    try:
        with open(preset_yml_path, 'r', encoding='utf-8') as f:
            preset_data = yaml.load(f, Loader=CustomLoader)

        if not isinstance(preset_data, dict):
            fail('preset.yml must be a YAML mapping')
            return

        if 'name' not in preset_data or 'description' not in preset_data:
            fail('preset.yml missing name or description')
        else:
            ok(f"preset.yml valid: {preset_data.get('name', 'N/A')}")
    except yaml.YAMLError as e:
        fail(f'preset.yml YAML parse error: {e}')
        return
    except Exception as e:
        fail(f'preset.yml read error: {e}')
        return

    # Parse agent.cordis.yml with actual YAML parser
    # The file contains a plugin list followed by optional configuration blocks
    # We need to parse them separately since they form an invalid single document
    try:
        with open(agent_yml_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split into plugin list and configuration sections
        # The config section starts with a specific top-level key (deepmemory:)
        # that appears at column 0 and is not part of the list
        lines = content.split('\n')
        list_lines = []
        config_lines = []
        in_config = False

        for i, line in enumerate(lines):
            # Check if this is the start of the deepmemory config block
            # (non-indented, starts with a word followed by :, not a list item)
            if not in_config and line.startswith('deepmemory:'):
                in_config = True

            if in_config:
                config_lines.append(line)
            else:
                list_lines.append(line)

        # Parse the plugin list
        list_yaml = '\n'.join(list_lines)
        agent_data = yaml.load(list_yaml, Loader=CustomLoader)

        if not isinstance(agent_data, list):
            fail('agent.cordis.yml must start with a YAML array of plugins')
            return

        # Validate that each entry has an 'id' field
        entries_with_id = [entry for entry in agent_data if isinstance(entry, dict) and 'id' in entry]
        if len(entries_with_id) == 0:
            fail('agent.cordis.yml has no entries with "id" field')
            return

        ok(f"agent.cordis.yml valid ({len(entries_with_id)} entries)")

        # Parse the configuration section if present
        deepmemory_config = None
        if config_lines:
            config_yaml = '\n'.join(config_lines)
            try:
                config_data = yaml.load(config_yaml, Loader=CustomLoader)
                if isinstance(config_data, dict) and 'deepmemory' in config_data:
                    deepmemory_config = config_data['deepmemory']
            except yaml.YAMLError:
                # Config section is optional or malformed
                pass

    except yaml.YAMLError as e:
        fail(f'agent.cordis.yml YAML parse error: {e}')
        return
    except Exception as e:
        fail(f'agent.cordis.yml read error: {e}')
        return

    # Extract all entry IDs and plugin names for capability checking
    entry_ids = set()
    plugin_names = set()
    nested_ids = set()
    for entry in agent_data:
        if isinstance(entry, dict):
            if 'id' in entry:
                entry_ids.add(entry['id'])
            if 'name' in entry:
                plugin_names.add(entry['name'])
            # Check nested config for sub-entries (like in groups)
            if 'config' in entry and isinstance(entry['config'], list):
                for sub_entry in entry['config']:
                    if isinstance(sub_entry, dict) and 'id' in sub_entry:
                        nested_ids.add(sub_entry['id'])

    all_ids = entry_ids | nested_ids

    # Check required capabilities with fuzzy matching
    # Capabilities can match: exact ID, part of ID, or in plugin name
    for cap in spec.get('required_capabilities', []):
        # Check exact match, prefix match (tool-xxx), or substring match
        found = (
            cap in all_ids or
            f'tool-{cap}' in all_ids or
            any(cap in id_name for id_name in all_ids) or
            any(cap in str(plugin) for plugin in plugin_names)
        )
        if not found:
            fail(f"missing required capability: {cap}")
        else:
            ok(f"has required capability: {cap}")

    # Check forbidden capabilities
    for cap in spec.get('forbidden_capabilities', []):
        found = (
            cap in all_ids or
            f'tool-{cap}' in all_ids or
            any(cap in id_name for id_name in all_ids) or
            any(cap in str(plugin) for plugin in plugin_names)
        )
        if found:
            fail(f"has forbidden capability: {cap}")
        else:
            ok(f"correctly excludes: {cap}")

    # Check memory plugin presence
    has_memory_plugin = 'harness-memory' in entry_ids
    if spec['must_have_memory'] and not has_memory_plugin:
        fail('missing harness-memory plugin')
    elif not spec['must_have_memory'] and has_memory_plugin:
        fail('should not have harness-memory plugin (blank template)')
    elif spec['must_have_memory']:
        ok('harness-memory plugin present')
    else:
        ok('correctly excludes deepmemory')

    # Check budget configuration (deepmemory_config was extracted during YAML parsing above)
    has_budget_config = deepmemory_config is not None

    if spec['must_have_budget'] and not has_budget_config:
        fail('missing budget configuration (deepmemory block)')
    elif spec['must_have_budget']:
        ok('budget configuration present')

        # Validate budget structure
        if not isinstance(deepmemory_config, dict):
            fail('deepmemory config must be a mapping')
        else:
            # Check preset_mode
            preset_mode = deepmemory_config.get('preset_mode')
            if spec['expected_preset_mode']:
                if preset_mode != spec['expected_preset_mode']:
                    fail(f"preset_mode should be '{spec['expected_preset_mode']}', got '{preset_mode}'")
                else:
                    ok(f"preset_mode correct: {preset_mode}")

            # Check context block
            context = deepmemory_config.get('context', {})
            if not isinstance(context, dict):
                fail('deepmemory.context must be a mapping')
            else:
                # Check soft_target_ratio and hard_limit_ratio
                soft_ratio = context.get('soft_target_ratio')
                hard_ratio = context.get('hard_limit_ratio')

                if soft_ratio is None:
                    fail('missing soft_target_ratio in budget')
                elif not isinstance(soft_ratio, (int, float)) or not (0 < soft_ratio <= 1):
                    fail(f'soft_target_ratio must be between 0 and 1, got {soft_ratio}')
                else:
                    ok(f'soft_target_ratio valid: {soft_ratio}')

                if hard_ratio is None:
                    fail('missing hard_limit_ratio in budget')
                elif not isinstance(hard_ratio, (int, float)) or not (0 < hard_ratio <= 1):
                    fail(f'hard_limit_ratio must be between 0 and 1, got {hard_ratio}')
                else:
                    ok(f'hard_limit_ratio valid: {hard_ratio}')

                # Check soft <= hard
                if soft_ratio is not None and hard_ratio is not None:
                    if soft_ratio > hard_ratio:
                        fail(f'soft_target_ratio ({soft_ratio}) must be <= hard_limit_ratio ({hard_ratio})')
                    else:
                        ok(f'soft <= hard constraint satisfied')

                # Check priority_allocation
                priority_alloc = context.get('priority_allocation', {})
                if not isinstance(priority_alloc, dict):
                    fail('priority_allocation must be a mapping')
                else:
                    # Check required budget fields
                    for field in spec.get('required_budget_fields', []):
                        if field not in priority_alloc:
                            fail(f"missing required budget field: {field}")
                        else:
                            ok(f"budget field present: {field}")

                    # Validate each allocation entry
                    priorities_seen = set()
                    for component, alloc in priority_alloc.items():
                        if not isinstance(alloc, dict):
                            fail(f'{component}: allocation must be a mapping')
                            continue

                        # Check ratio
                        ratio = alloc.get('ratio')
                        if ratio is None:
                            fail(f'{component}: missing ratio')
                        elif not isinstance(ratio, (int, float)) or not (0 <= ratio <= 1):
                            fail(f'{component}: ratio must be between 0 and 1, got {ratio}')

                        # Check priority
                        priority = alloc.get('priority')
                        if priority is None:
                            fail(f'{component}: missing priority')
                        elif not isinstance(priority, int) or priority < 1:
                            fail(f'{component}: priority must be a positive integer, got {priority}')
                        else:
                            if priority in priorities_seen:
                                fail(f'{component}: priority {priority} already used (priorities must be unique)')
                            priorities_seen.add(priority)

                        # Check min_tokens
                        min_tokens = alloc.get('min_tokens')
                        if min_tokens is None:
                            fail(f'{component}: missing min_tokens')
                        elif not isinstance(min_tokens, int) or min_tokens < 0:
                            fail(f'{component}: min_tokens must be a non-negative integer, got {min_tokens}')

    # Check persona exists
    has_persona = 'persona' in entry_ids
    if not has_persona:
        fail('missing persona configuration')
    else:
        ok('persona configured')

# Run checks
print('Preset contract verification\n')
for spec in PRESETS:
    check_preset(spec)

print('\n' + '=' * 60)
if failures == 0:
    print('✓ All preset checks passed')
    sys.exit(0)
else:
    print(f'✗ {failures} check(s) failed')
    sys.exit(1)
