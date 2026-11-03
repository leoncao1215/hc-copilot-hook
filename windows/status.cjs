'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');

let lockFd;
let lockPath;
let temporary;

try {
  const terminal = process.env.HC_TERMINAL_ID;
  const socket = process.env.HC_TERMINAL_SOCKET;
  const scope = process.env.HC_SCOPE;
  if (process.platform !== 'win32' || !/^t[0-9a-z]+$/.test(terminal || '') ||
      !socket?.startsWith('\\\\.\\pipe\\herdr-core-') || !scope) process.exit(0);

  const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (['agentId', 'agent_id', 'parentAgentId', 'parent_agent_id', 'agentType', 'agent_type']
    .some(key => payload[key] != null && payload[key] !== '')) process.exit(0);

  const session = payload.sessionId || payload.session_id;
  if (typeof session !== 'string' || !session || session.length > 256) process.exit(0);
  const event = process.argv[2];
  let state;
  let kind = '';
  switch (event) {
    case 'sessionStart': state = 'idle'; break;
    case 'userPromptSubmitted':
    case 'postToolUse':
    case 'postToolUseFailure': state = 'working'; break;
    case 'agentStop': state = 'done'; break;
    case 'sessionEnd': state = 'clear'; break;
    case 'notification':
      if (payload.notification_type === 'permission_prompt') kind = 'permission';
      else if (payload.notification_type === 'elicitation_dialog') kind = 'question';
      else process.exit(0);
      state = 'blocked';
      break;
    default: process.exit(0);
  }

  const timestamp = typeof payload.timestamp === 'number'
    ? payload.timestamp : Date.parse(payload.timestamp);
  if (!Number.isFinite(timestamp)) process.exit(0);
  const key = crypto.createHash('sha256').update(`${socket}\0${scope}\0${terminal}`).digest('hex');
  const directory = path.join(__dirname, 'runtime');
  fs.mkdirSync(directory, { recursive: true });
  const file = path.join(directory, `${key}.json`);
  lockPath = path.join(directory, `${key}.lock`);
  try {
    lockFd = fs.openSync(lockPath, 'wx');
  } catch (error) {
    if (error.code === 'EEXIST' && Date.now() - fs.statSync(lockPath).mtimeMs > 5000) {
      fs.unlinkSync(lockPath);
      lockFd = fs.openSync(lockPath, 'wx');
    } else throw error;
  }
  let previous;
  try { previous = JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }

  // exec hooks launch directly from Copilot; a nested CLI in the same pane is not its owner.
  const ownerPid = process.ppid;
  if (previous) {
    let alive = true;
    try { process.kill(previous.ownerPid, 0); }
    catch (error) { alive = error.code !== 'ESRCH'; }
    if (previous.ownerPid !== ownerPid && alive) return;
    if (previous.ownerPid === ownerPid && previous.session !== session && event !== 'sessionStart') return;
    if (previous.ownerPid !== ownerPid && event === 'sessionEnd') return;
    if (timestamp < previous.timestamp) return;
  } else if (event === 'sessionEnd') return;

  const result = spawnSync(path.join(__dirname, 'ConsoleReport.exe'),
    [String(ownerPid), state, kind],
    { stdio: 'ignore', windowsHide: true, timeout: 300 });
  if (result.error || result.status !== 0) return;

  if (event === 'sessionEnd') fs.unlinkSync(file);
  else {
    temporary = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({ terminal, session, ownerPid, timestamp, event, state, kind }));
    fs.renameSync(temporary, file);
    temporary = undefined;
  }
} catch {
  // This integration is observational: errors never produce hook decisions or a nonzero exit.
} finally {
  if (temporary) {
    try { fs.unlinkSync(temporary); } catch {}
  }
  if (lockFd !== undefined) {
    try { fs.closeSync(lockFd); } catch {}
    try { fs.unlinkSync(lockPath); } catch {}
  }
}
