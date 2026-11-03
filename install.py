"""Install/uninstall user-level hooks without rewriting other Copilot settings."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile


SOURCE = Path(__file__).resolve().parent
BEGIN = "# BEGIN hc-copilot-hook"
END = "# END hc-copilot-hook"
FILES = ("bridge.py", "startup.py", "copilot.zsh")
EVENTS = (
    "sessionStart", "userPromptSubmitted", "notification", "agentStop",
    "postToolUse", "postToolUseFailure", "preCompact", "errorOccurred", "sessionEnd",
)


def read_file(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Refusing to modify a symlink or non-file: {path}")
    return path.read_bytes() if path.exists() else None


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def shell_without_block(text):
    if BEGIN not in text and END not in text:
        return text
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("Ambiguous hc-copilot-hook shell markers; inspect the file first")
    start = text.index(BEGIN)
    finish = text.index(END) + len(END)
    if finish <= start:
        raise ValueError("Invalid hc-copilot-hook shell marker order")
    if text[finish:finish + 1] == "\n":
        finish += 1
    return text[:start] + text[finish:]


def hook_config(python, runtime):
    command = f"{shlex.quote(str(python))} -I {shlex.quote(str(runtime / 'bridge.py'))}"
    hooks = {}
    for event in EVENTS:
        entry = {
            "type": "command",
            "bash": f"{command} {event} >/dev/null 2>&1 || true",
            "timeoutSec": 1,
        }
        if event == "notification":
            entry["matcher"] = "permission_prompt|elicitation_dialog"
        hooks[event] = [entry]
    return {"version": 1, "hooks": hooks}


def save_backup(home, originals):
    directory = home / "backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="hc-copilot-hook-", dir=directory))
    index = {}
    for number, (path, content) in enumerate(originals.items()):
        if content is not None:
            name = f"{number}-{path.name}"
            atomic_write(backup / name, content)
            index[name] = str(path)
    atomic_write(backup / "index.json", json.dumps(index, indent=2).encode() + b"\n")
    return backup


def paths(home):
    runtime = home / "herdr-app-status"
    return runtime, home / "hooks/herdr-app-status.json", runtime / "installation.json"


def install(home, zshrc=None):
    runtime, hooks, manifest_path = paths(home)
    if runtime.is_symlink() or hooks.parent.is_symlink():
        raise ValueError("Refusing symlinked integration directories")
    previous_bytes = read_file(manifest_path)
    previous = json.loads(previous_bytes) if previous_bytes else {}
    if previous and previous.get("package") != "hc-copilot-hook":
        raise ValueError("Unrecognized installation manifest")
    old_zshrc = previous.get("zshrc")
    if old_zshrc:
        if zshrc and str(zshrc) != old_zshrc:
            raise ValueError("Uninstall before changing the managed zshrc path")
        zshrc = Path(old_zshrc)
    python = Path(sys.executable).absolute()
    writes = {runtime / name: (SOURCE / name).read_bytes() for name in FILES[:2]}
    template = (SOURCE / "copilot.zsh.in").read_text()
    wrapper = template.replace("@PYTHON@", shlex.quote(str(python)))
    wrapper = wrapper.replace("@STARTUP@", shlex.quote(str(runtime / "startup.py")))
    writes[runtime / "copilot.zsh"] = wrapper.encode()
    writes[hooks] = json.dumps(hook_config(python, runtime), indent=2).encode() + b"\n"
    if not previous:
        for path in writes:
            if read_file(path) is not None:
                raise ValueError(f"Unmanaged file already exists; inspect before installing: {path}")
    hashes = {str(path.relative_to(home)): hashlib.sha256(data).hexdigest()
              for path, data in writes.items()}
    if zshrc:
        existing = (read_file(zshrc) or b"").decode()
        text = shell_without_block(existing)
        source = shlex.quote(str(runtime / "copilot.zsh"))
        block = f'{BEGIN}\n[[ -r {source} ]] && source {source}\n{END}\n'
        writes[zshrc] = (text + ("" if not text or text.endswith("\n") else "\n") + block).encode()
    manifest = {
        "package": "hc-copilot-hook", "version": 1, "files": hashes,
        "zshrc": str(zshrc) if zshrc else None,
    }
    writes[manifest_path] = json.dumps(manifest, indent=2).encode() + b"\n"
    originals = {path: read_file(path) for path in writes}
    backup = save_backup(home, originals)
    for path, content in writes.items():
        mode = stat.S_IMODE(path.stat().st_mode) if path == zshrc and path.exists() else 0o600
        atomic_write(path, content, mode)
    print(f"Installed: {hooks}\nBackup: {backup}")
    if zshrc:
        print(f"Open a new zsh, or source {shlex.quote(str(runtime / 'copilot.zsh'))}")
    else:
        print("Hooks only: initial idle before the first message requires the zsh wrapper.")
    print("Restart existing Copilot processes; Herdr App and hc do not need a restart.")


def uninstall(home):
    runtime, hooks, manifest_path = paths(home)
    if runtime.is_symlink() or hooks.parent.is_symlink():
        raise ValueError("Refusing symlinked integration directories")
    content = read_file(manifest_path)
    if content is None:
        raise ValueError("No installation manifest; refusing to remove an unmanaged installation")
    manifest = json.loads(content)
    if manifest.get("package") != "hc-copilot-hook":
        raise ValueError("Unrecognized installation manifest")
    managed = [runtime / name for name in FILES] + [hooks]
    originals = {path: read_file(path) for path in managed + [manifest_path]}
    for path in managed:
        existing = originals[path]
        expected = manifest["files"].get(str(path.relative_to(home)))
        if existing is not None and hashlib.sha256(existing).hexdigest() != expected:
            raise ValueError(f"Local edits found; inspect before uninstalling: {path}")
    zshrc = Path(manifest["zshrc"]) if manifest.get("zshrc") else None
    shell = None
    if zshrc:
        originals[zshrc] = read_file(zshrc)
        if originals[zshrc] is not None:
            shell = shell_without_block(originals[zshrc].decode()).encode()
    backup = save_backup(home, originals)
    if shell is not None:
        atomic_write(zshrc, shell, stat.S_IMODE(zshrc.stat().st_mode))
    for path in managed + [manifest_path]:
        if originals[path] is not None:
            path.unlink()
    print(f"Uninstalled. Backup: {backup}")
    print("Other hooks/settings, backup files, and status metadata were left untouched.")
    print("Restart Copilot and open a new shell (or run: unfunction copilot).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--copilot-home", type=Path,
                        default=Path(os.environ.get("COPILOT_HOME", "~/.copilot")))
    parser.add_argument("--zshrc", type=Path, help="Opt in to the initial-idle zsh wrapper")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This integration is currently supported/tested on macOS only.")
    home = args.copilot_home.expanduser().absolute()
    zshrc = args.zshrc.expanduser().absolute() if args.zshrc else None
    try:
        if args.action == "install":
            install(home, zshrc)
        elif zshrc:
            parser.error("Uninstall uses the zshrc recorded during installation.")
        else:
            uninstall(home)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
