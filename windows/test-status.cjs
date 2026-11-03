'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('root lifecycle, child/nested ownership, timestamps and reporter failure stay observational', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'hc-state-test-'));
  try {
    const source = path.join(directory, 'Fake.cs');
    const output = path.join(directory, 'report.txt');
    fs.writeFileSync(source, 'using System;using System.IO;class Fake{static int Main(string[] a){File.WriteAllText(Environment.GetEnvironmentVariable("HC_TEST_REPORT"),string.Join("|",a));return 0;}}');
    const compiler = path.join(process.env.WINDIR, 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe');
    assert.equal(spawnSync(compiler, ['/nologo', '/target:exe', `/out:${path.join(directory, 'ConsoleReport.exe')}`, source]).status, 0);
    fs.copyFileSync(path.join(__dirname, 'status.cjs'), path.join(directory, 'status.cjs'));
    const env = { ...process.env, HC_TERMINAL_ID: 't999', HC_SCOPE: 'offline-test',
      HC_TERMINAL_SOCKET: '\\\\.\\pipe\\herdr-core-offline-test', HC_TEST_REPORT: output };
    let stamp = Date.now();
    const invoke = (event, extra = {}, script = path.join(directory, 'status.cjs')) => {
      const result = spawnSync(process.execPath, [script, event], {
        env, input: JSON.stringify({ sessionId: 'root', timestamp: ++stamp, ...extra }), encoding: 'utf8', timeout: 1500,
      });
      assert.equal(result.status, 0);
      assert.equal(result.stdout, '');
      assert.equal(result.stderr, '');
    };
    invoke('sessionStart');
    assert.match(fs.readFileSync(output, 'utf8'), /\|idle\|$/);
    invoke('userPromptSubmitted');
    assert.match(fs.readFileSync(output, 'utf8'), /\|working\|$/);
    invoke('notification', { notification_type: 'elicitation_dialog' });
    assert.match(fs.readFileSync(output, 'utf8'), /\|blocked\|question$/);
    const blocked = fs.readFileSync(output, 'utf8');
    for (const field of ['agentId', 'agent_id', 'agentType', 'agent_type', 'parentAgentId', 'parent_agent_id']) {
      invoke('agentStop', { [field]: 'child' });
      assert.equal(fs.readFileSync(output, 'utf8'), blocked);
    }
    invoke('notification', { notification_type: 'agent_completed' });
    invoke('notification', { notification_type: 'permission_prompt', timestamp: stamp - 10000 });
    invoke('agentStop', { sessionId: 'old' });
    assert.equal(fs.readFileSync(output, 'utf8'), blocked);
    const nested = spawnSync(process.execPath, ['-e', `require('node:child_process').spawnSync(process.execPath,[${JSON.stringify(path.join(directory, 'status.cjs'))},'sessionStart'],{input:${JSON.stringify(JSON.stringify({ sessionId: 'nested', timestamp: ++stamp }))},env:process.env})`], { env });
    assert.equal(nested.status, 0);
    assert.equal(fs.readFileSync(output, 'utf8'), blocked);
    invoke('postToolUse');
    assert.match(fs.readFileSync(output, 'utf8'), /\|working\|$/);
    invoke('agentStop');
    assert.match(fs.readFileSync(output, 'utf8'), /\|done\|$/);
    fs.unlinkSync(path.join(directory, 'ConsoleReport.exe'));
    invoke('postToolUse');
    assert.match(fs.readFileSync(output, 'utf8'), /\|done\|$/);
  } finally { fs.rmSync(directory, { recursive: true }); }
});
