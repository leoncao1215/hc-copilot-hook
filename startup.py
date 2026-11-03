"""Report initial idle status for foreground interactive zsh launches only."""

import os
from pathlib import Path
import signal
import subprocess
import sys


VALUE_OPTIONS = {
    "-i", "--interactive", "-C", "-n", "--name", "--session-id",
    "--model", "--agent", "--reasoning-effort", "--context", "--auto-tier",
    "--mode", "--add-dir", "--plugin-dir", "--log-dir", "--log-level",
    "--max-ai-credits", "--max-autopilot-continues",
}
OPTIONAL_OPTIONS = {"-r", "--resume", "--connect", "--mouse", "--bash-env"}
FLAGS = {
    "--continue", "--plan", "--autopilot", "--fleet", "--banner",
    "--screen-reader", "--plain-diff", "--experimental", "--no-experimental",
    "--no-mouse", "--no-bash-env", "--no-color", "--disable-client-telemetry",
    "--disable-builtin-mcps", "--assisted-approval", "--allow-all",
    "--allow-all-tools", "--allow-all-paths", "--allow-all-urls", "--yolo",
}


def interactive_args(args):
    # Unrecognized invocations use lifecycle hooks only. Argument inspection
    # never changes the arguments passed to the real CLI.
    index = 0
    while index < len(args):
        option, equals, _ = args[index].partition("=")
        if option in VALUE_OPTIONS:
            if not equals:
                index += 1
                if index == len(args) or args[index].startswith("-"):
                    return False
        elif option in OPTIONAL_OPTIONS:
            if not equals and index + 1 < len(args) and not args[index + 1].startswith("-"):
                index += 1
        elif option not in FLAGS or equals:
            return False
        index += 1
    return True


def allowed_process(tty_fd):
    if os.getpgrp() != os.tcgetpgrp(tty_fd):
        return False
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,comm="],
        capture_output=True, text=True, timeout=0.15, check=True,
    )
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3:
            processes[int(fields[0])] = (int(fields[1]), Path(fields[2]).name)
    pid = os.getppid()
    if processes.get(pid, (0, ""))[1] != "zsh":
        return False
    for _ in range(64):
        parent, command = processes.get(pid, (0, ""))
        if command == "copilot":
            return False
        if parent <= 1:
            return True
        if parent == pid:
            return False
        pid = parent
    return False


def main():
    signal.signal(signal.SIGALRM, lambda *_: os._exit(1))
    signal.setitimer(signal.ITIMER_REAL, 0.4)
    try:
        if not os.environ.get("HC_TERMINAL_ID"):
            return 1
        action = sys.argv[1]
        if action not in ("start", "stop"):
            return 1
        if action == "start" and not interactive_args(sys.argv[2:]):
            return 1
        fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if not allowed_process(fd):
                return 1
            state = "idle" if action == "start" else "clear"
            os.write(fd, f"\x1b]7501;state={state}:app=copilot\x1b\\".encode("ascii"))
        finally:
            os.close(fd)
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
