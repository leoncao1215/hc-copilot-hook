# Agent instructions

## Purpose and scope

This repository integrates **Copilot CLI with Herdr App / herdr-core (`hc`)**.
It is not the old Herdr TUI's `herdr integration` API.
Read README.md before installing, changing, or diagnosing this integration.

Supported/tested: macOS, zsh, Python 3 standard library, native `copilot` binary.
Reference versions: Copilot 1.0.105, hc 0.1.17, Herdr App 0.1.12.
Windows/Dev Box has a separate hooks-only adapter under `windows/`; read
`windows/README.md` before deployment. Its lifecycle helper attaches the
launching process's Windows console, not `/dev/tty`. It must not wrap copilot,
add shell functions/aliases, modify profiles or use the macOS installer.
Fresh startup/resume before the first message is NOT supported by the Windows
hooks-only adapter in the tested CLI version; do not claim otherwise.
Do not claim Linux, another shell, or Node-hosted CLI support without
implementing and verifying their process/PTY semantics.

## Before installing

1. Identify where Copilot and hc actually run. The App may only be a remote viewer.
2. Check installed versions and current official integration support first.
   Prefer native support if it now covers the requirements; avoid duplicate reporters.
3. Inspect existing user hooks, `COPILOT_HOME`, shell configuration, and
   `whence -va copilot`. Do not expose tokens or unrelated configuration contents.
4. Get consent before adding/replacing a shell function or changing `.zshrc`.
   Existing aliases/functions may require manual integration.
5. Use `python3 install.py install --zshrc "${ZDOTDIR:-$HOME}/.zshrc"` for startup
   coverage, or omit `--zshrc` for hooks only. The installer backs up edited files.
   Never overwrite an unrecognized installation just to make the command succeed.
6. On Windows use PowerShell 7 `windows\install.ps1`. It only writes independent
   user-level hooks and owned helper files, never profiles or command wrappers.
   Existing Copilot processes must exit/resume to load new hooks: installing a
   file during a turn does not retroactively activate reporting in that process.

## Architecture and invariants

- `bridge.py`: consumes official camelCase lifecycle hook payloads, writes OSC 7501
  directly to its controlling PTY, and tracks owner/session/timestamp locally.
  macOS relaunches may keep the original foreground group leader alive as a
  parent. Only an unambiguous direct Copilot chain may inherit its ownership;
  reject shell-mediated nested CLIs and late hooks from the waiting parent.
  Root userPromptSubmitted can establish session identity if Start was missed.
- `startup.py`: reports initial idle for eligible foreground zsh launches. It
  must reject nested/background agent processes and noninteractive CLI modes.
- `copilot.zsh.in`: installer-rendered shell function; never replaces the official
  binary. Preserve argv, stdin/stdout, exit code, and normal approval behavior.
- `install.py`: copies independent user-level hook files; never rewrites
  `settings.json` or removes unrelated hooks. Uninstall removes only owned files
  and the marked shell block, preserving later unrelated shell edits.

Never:

- Add `preToolUse` or `permissionRequest` hooks for status reporting.
- Return approval decisions, force continuation, or inject prompts/model context.
- Type a synthetic initial message to make an idle session show up.
- Write OSC bytes to hook stdout: Copilot parses stdout as hook return data.
- Share one global state record across panes or accept child completion as root completion.
- Depend only on inherited `HC_TERMINAL_ID`; check controlling PTY, foreground
  process ownership, ancestry, child identifiers and session identity.
- Block on a state lock, retry indefinitely, or relax sandbox/permissions to send status.
- Treat `agentStop` as proof that all background work finished.
- Claim Esc/cancellation or concurrent-dialog state is fully supported.

## Validation

Run from the repository root:

```sh
python3 -B -m unittest discover -s tests -v
git diff --check
```

Default tests must remain offline, use temporary directories/PTYs, and never run
live models or change actual user settings.

For approved live checks, create **new isolated test terminals** and record their
returned IDs. Never resume a user's active conversation in a second process.
Use a stopped test conversation for resume verification.

Check **actual received state** with `hc get <test-terminal> --json` and the
App's `herdr-app ls`, not just hook exit status:

1. Fresh `copilot` and `--resume` appear as idle before the first message.
2. A real turn transitions working to done; App may normalize viewed done to idle.
3. An actual question yields blocked/question; an actual permission prompt yields
   blocked/permission. Only approve an explicitly harmless test action once.
4. Two terminals remain independent. Background-child completion must not clear
   a main session's pending question.
5. Reporting failure must not prevent a response or alter tool approval.
6. Exiting before any message clears initial status.

Read the screen before sending anything to a blocked pane. Never automatically
use `hc send --force` merely because a status might be stale.
Remove only the exact test space/terminal IDs created for the check. Never stop
the shared core or close existing user panes.

## Handoff

Report what actually passed and what remains unsupported. Explain:

- New shells load the startup wrapper automatically; existing shells can source
  the installed `herdr-app-status/copilot.zsh`.
- Existing Copilot processes should exit/resume to reload hooks.
- Herdr App/core do not need restarting.
- Absolute-path, `command copilot`, and `env ... copilot` launches bypass the
  shell function, and unknown CLI options deliberately skip initial reporting.
- Config, backup and uninstall locations (see README.md).

## Credentials and repository hygiene

Deploy keys are separate from this integration. Generate them only when the user
requests it. Store private keys outside the checkout with restrictive permissions;
only public keys may be displayed/registered. Keep git SSH configuration local to
this repository, and verify the remote commit after an authorized push.

Never commit private keys, access tokens, `.copilot` settings, hook payloads,
user prompts, terminal captures, config backups, or per-session state files.
