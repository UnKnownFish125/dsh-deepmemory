// Preset contract verification: checks that task/daily/blank presets exist,
// are valid YAML, have required fields, and meet capability/budget requirements.

import { readFileSync, existsSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const ROOT = join(__dirname, '../..');

const PRESETS = [
  {
    name: 'task',
    path: 'agent-preset/task',
    requiredCapabilities: ['subagent', 'workflow', 'plan-mode', 'todo', 'delegation'],
    requiredBudgetFields: ['task_state_card', 'task_board', 'active_memory'],
    mustHaveMemory: true,
    mustHaveBudget: true,
  },
  {
    name: 'daily',
    path: 'agent-preset/daily',
    requiredCapabilities: ['memory'],
    forbiddenCapabilities: ['subagent', 'workflow', 'todo', 'delegation'],
    requiredBudgetFields: ['daily_state_card', 'active_memory'],
    mustHaveMemory: true,
    mustHaveBudget: true,
  },
  {
    name: 'blank',
    path: 'agent-preset/blank-template',
    requiredCapabilities: [],
    forbiddenCapabilities: ['harness-memory'],
    mustHaveMemory: false,
    mustHaveBudget: false,
  },
];

let failures = 0;

function fail(msg) {
  console.error(`  ✗ ${msg}`);
  failures++;
}

function pass(msg) {
  console.log(`  ✓ ${msg}`);
}

// Simple YAML parser for basic key-value structures
function parseSimpleYaml(content) {
  const lines = content.split('\n');
  const result = {};
  for (const line of lines) {
    const match = line.match(/^(\w+):\s*(.+)$/);
    if (match) {
      result[match[1]] = match[2].trim();
    }
  }
  return result;
}

function checkPreset(spec) {
  console.log(`\n[${spec.name} preset]`);

  const presetYmlPath = join(ROOT, spec.path, 'preset.yml');
  const agentYmlPath = join(ROOT, spec.path, 'agent.cordis.yml');

  // Check files exist
  if (!existsSync(presetYmlPath)) {
    fail(`preset.yml not found at ${spec.path}`);
    return;
  }
  if (!existsSync(agentYmlPath)) {
    fail(`agent.cordis.yml not found at ${spec.path}`);
    return;
  }
  pass('preset.yml and agent.cordis.yml exist');

  // Parse preset.yml
  let presetContent;
  try {
    presetContent = readFileSync(presetYmlPath, 'utf-8');
    const presetMeta = parseSimpleYaml(presetContent);
    if (!presetMeta.name || !presetMeta.description) {
      fail('preset.yml missing name or description');
    } else {
      pass(`preset.yml valid: ${presetMeta.name}`);
    }
  } catch (err) {
    fail(`preset.yml read error: ${err.message}`);
    return;
  }

  // Parse agent.cordis.yml
  let agentContent;
  try {
    agentContent = readFileSync(agentYmlPath, 'utf-8');
    // Basic check: should have YAML array structure with id: entries
    if (!agentContent.includes('- id:') && !agentContent.includes('-id:')) {
      fail('agent.cordis.yml does not appear to be a valid config array');
      return;
    }
    const entryCount = (agentContent.match(/- id:/g) || []).length;
    pass(`agent.cordis.yml valid (${entryCount} entries)`);
  } catch (err) {
    fail(`agent.cordis.yml read error: ${err.message}`);
    return;
  }

  // Check required capabilities
  for (const cap of spec.requiredCapabilities || []) {
    const found = agentContent.includes(cap);
    if (!found) {
      fail(`missing required capability: ${cap}`);
    } else {
      pass(`has required capability: ${cap}`);
    }
  }

  // Check forbidden capabilities
  for (const cap of spec.forbiddenCapabilities || []) {
    const found = agentContent.includes(cap);
    if (found) {
      fail(`has forbidden capability: ${cap}`);
    } else {
      pass(`correctly excludes: ${cap}`);
    }
  }

  // Check memory plugin
  const hasMemoryPlugin = agentContent.includes('harness-memory') ||
                          agentContent.includes('memory-plugin');
  if (spec.mustHaveMemory && !hasMemoryPlugin) {
    fail('missing harness-memory plugin');
  } else if (!spec.mustHaveMemory && hasMemoryPlugin) {
    fail('should not have harness-memory plugin (blank template)');
  } else if (spec.mustHaveMemory) {
    pass('harness-memory plugin present');
  } else {
    pass('correctly excludes deepmemory');
  }

  // Check budget configuration
  const hasBudgetConfig = agentContent.includes('budget_profile') ||
                          agentContent.includes('priority_allocation') ||
                          agentContent.includes('soft_target_ratio');

  if (spec.mustHaveBudget && !hasBudgetConfig) {
    fail('missing budget configuration');
  } else if (spec.mustHaveBudget) {
    pass('budget configuration present');

    // Check specific budget fields
    for (const field of spec.requiredBudgetFields || []) {
      if (!agentContent.includes(field)) {
        fail(`missing budget field: ${field}`);
      } else {
        pass(`budget field present: ${field}`);
      }
    }

    // Check for required budget structure
    if (!agentContent.includes('soft_target_ratio')) {
      fail('missing soft_target_ratio in budget');
    }
    if (!agentContent.includes('hard_limit_ratio')) {
      fail('missing hard_limit_ratio in budget');
    }
    if (!agentContent.includes('priority_allocation')) {
      fail('missing priority_allocation in budget');
    }
  }

  // Check persona exists
  const hasPersona = agentContent.includes('id: persona');
  if (!hasPersona) {
    fail('missing persona configuration');
  } else {
    pass('persona configured');
  }
}

// Run checks
console.log('Preset contract verification\n');
for (const spec of PRESETS) {
  checkPreset(spec);
}

console.log('\n' + '='.repeat(60));
if (failures === 0) {
  console.log('✓ All preset checks passed');
  process.exit(0);
} else {
  console.log(`✗ ${failures} check(s) failed`);
  process.exit(1);
}
