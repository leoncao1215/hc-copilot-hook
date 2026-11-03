# Windows: agent deployment guide (hooks only)

This community adapter connects Copilot CLI to herdr-core / Herdr App. Install
on the **Windows machine running Copilot and hc**, not on a Mac that merely
views the remote terminals. It does not use the old Herdr TUI API.

**No copilot wrapper, function, alias, profile edit, synthetic prompt, polling
daemon or changed tool approval policy is installed.**

## Agent preflight and installation

Read the repository `AGENTS.md`, installed `hc --skill`, `herdr-app --skill`,
and current [official hook reference](https://docs.github.com/en/copilot/reference/hooks-reference).
Prefer newer native integration if it now covers the requirements; avoid
duplicate reporters. Check `copilot --version`, `hc --version`,
`node --version`, `pwsh --version`, `COPILOT_HOME` and existing user hooks.
Do not expose unrelated config contents, credentials or conversation data.

Run in PowerShell 7 from a checkout:

```powershell
.\windows\install.ps1
# Non-default home; COPILOT_HOME is also respected automatically.
.\windows\install.ps1 -CopilotHome 'D:\Copilot home'
```

The installer resolves this machine's Node executable and home paths. It uses
Node built-ins and the existing Windows .NET Framework C# compiler, downloads
no dependencies and compiles the console helper locally. Source only is
committed; there are no author's usernames/paths, deploy keys or executables
in this directory.

Existing settings/permissions, unrelated hooks, old TUI scripts and shell
configuration are untouched. Dedicated files are backed up before changes;
unrecognized files, links and modified managed files are refused.

For the earlier manual Windows prototype ONLY, inspect the exact helper and
hook files before opting into migration:

```powershell
.\windows\install.ps1 -AdoptPrototype
```

Do not use this flag to overwrite an unknown installation. Individual file
replacement is atomic, not the whole multi-file update; on failure inspect the
output and private backup index. Never upload/commit configuration backups.

## Lifecycle and explicit initial-idle limitation

Reference versions: native Copilot CLI 1.0.105, hc 0.1.17, Windows x64.

| CLI event | Core status |
| --- | --- |
| `sessionStart`, when the CLI actually emits it | `idle` |
| `userPromptSubmitted` | `working` (busy) |
| `postToolUse` / `postToolUseFailure` | `working` |
| `notification: permission_prompt` | `blocked`, `kind=permission` |
| `notification: elicitation_dialog` | `blocked`, `kind=question` |
| main `agentStop` | `done` |
| `sessionEnd`, when emitted | `clear` |

**Fresh startup or resume before the first message is NOT reliably supported
by hooks-only in this version.** A plain no-prompt startup was verified to
produce no status events. Declaring a `sessionStart` hook does not force an
event before the first user message; the logical session can be created later.
No verified official independent process-start hook was found. Do not send a
fake message, wrap the command or label this limitation fixed.

**Installation during a running session does not activate its hooks.**
Exit and resume Copilot (or use its supported restart command) after install.
A busy state missing in a pre-install process is not fixed by manually writing
OSC once; validate a new process's automatic lifecycle instead.
Herdr App/core need no restart; never stop the shared core just to reload hooks.

## Isolation, receipt verification and boundaries

Reports go to the hook-launching process's own Windows console using
`AttachConsole` / `CONOUT$`, not to captured hook stdout or a focused pane.
Metadata is separated by endpoint, HC scope and terminal. Child identity
fields, background-completion notifications, stale sessions/timestamps and
nested CLI ownership conflicts are ignored. Only nonsecret lifecycle metadata
is stored, never prompts or tool results.

The native reporter has a 300 ms timeout and each hook a 1 s timeout. Locks do
not wait. Failures are silent and the hook returns no decisions, model context
or nonzero exit. There are no `preToolUse` or `permissionRequest` hooks.
High load, unavailable executables, contention and sandbox restrictions can
lose status reports; do not weaken permissions or sandbox rules to fix that.

Use new isolated test terminals, record their returned IDs and inspect actual
receipts with `hc get <test-terminal> --json` / `hc events`. Check the App with
`herdr-app ls` on the machine actually running that window. A remote Mac App
does not imply the Windows host's App CLI can connect to a local window.

Verify a real turn transitions `working` to `done`, and an actual question
transitions to `blocked/question`. A true permission prompt needs specific
consent for one harmless action; never turn on global auto-approval for tests.
Keep two terminals independent and complete a background child while the root
waits for input. Inspect the screen before answering a blocked test terminal;
never blindly use `hc send --force`. Remove only terminals you created.

`done` means the root turn stopped, not all background work completed. App may
display viewed done as idle. Login/trust dialogs, Esc/cancellation, forced
termination, concurrent dialogs and other Stop hooks forcing continuation
remain incomplete. Upstream notifications lacking child provenance cannot be
perfectly attributed. State exactly which live checks passed; event replay is
not a real permission-prompt or GUI check.

## Files, update, uninstall and tests

```text
%USERPROFILE%\.copilot\hooks\hc-status.json
%USERPROFILE%\.copilot\herdr-app-state\
    status.cjs, ConsoleReport.cs, ConsoleReport.exe, installation.json, runtime\
%USERPROFILE%\.copilot\backups\hc-copilot-hook-windows-*\
```

These paths follow `COPILOT_HOME` / `-CopilotHome`. Update by rerunning the
installer on EACH target machine; never copy another machine's generated JSON.

```powershell
.\windows\install.ps1 -Action uninstall
python -B -m unittest discover -s tests -v
git diff --check
```

Uninstall validates hashes, backs up first and removes only manifest-owned
files. Settings, other hooks, backups and runtime metadata are retained.
Restart existing Copilot processes after install/update/uninstall.
Default tests are offline and use temporary homes; macOS PTY tests are skipped
on Windows. Do not commit user config, SSH private keys, captures or state.
