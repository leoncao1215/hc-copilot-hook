"""Best-effort Copilot lifecycle reporting to the current herdr-core PTY."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys


STATE_DIR = Path(__file__).parent / "state"
CHILD_FIELDS = (
    "agentId", "agent_id", "agentType", "agent_type",
    "parentAgentId", "parent_agent_id",
)


def transition(previous, event, payload):
    if not isinstance(payload, dict) or any(payload.get(k) for k in CHILD_FIELDS):
        return None
    session = payload.get("sessionId")
    timestamp = payload.get("timestamp")
    if not isinstance(session, str) or not session:
        return None
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp <= 0):
        return None
    if previous and timestamp < previous["timestamp"]:
        return None
    if event != "sessionStart" and (
        not previous or previous["session"] != session
    ):
        return None

    kind = None
    if event == "sessionStart":
        state = "idle"
    elif event in (
        "userPromptSubmitted", "postToolUse", "postToolUseFailure", "preCompact"
    ):
        state = "working"
    elif event == "notification":
        kind = {
            "permission_prompt": "permission",
            "elicitation_dialog": "question",
        }.get(payload.get("notification_type"))
        if kind is None:
            return None
        state = "blocked"
    elif event == "agentStop" and payload.get("stopReason") == "end_turn":
        state = "done"
    elif event == "errorOccurred" and payload.get("recoverable") is False:
        state = "error"
    elif event == "sessionEnd":
        state = "clear"
    else:
        return None

    return {
        "session": session, "timestamp": timestamp,
        "state": state, "kind": kind, "event": event,
    }


def foreground_copilot(tty_fd):
    foreground = os.tcgetpgrp(tty_fd)
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,comm="],
        capture_output=True, text=True, timeout=0.15, check=True,
    )
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3:
            processes[int(fields[0])] = (int(fields[1]), fields[2])
    pid = os.getppid()
    for _ in range(32):
        parent, command = processes.get(pid, (0, ""))
        if Path(command).name == "copilot":
            # Nested/background CLIs inherit HC_TERMINAL_ID but do not own
            # the foreground Copilot process group of this PTY.
            return pid if pid == foreground else None
        if parent <= 1 or parent == pid:
            break
        pid = parent
    return None


def emit(tty_fd, record):
    message = f"\x1b]7501;state={record['state']}:app=copilot"
    if record["kind"]:
        message += f":kind={record['kind']}"
    os.write(tty_fd, (message + "\x1b\\").encode("ascii"))


def report(event, payload):
    terminal = os.environ.get("HC_TERMINAL_ID")
    if not terminal or not isinstance(payload, dict):
        return
    if any(payload.get(k) for k in CHILD_FIELDS):
        return
    tty_fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        owner = foreground_copilot(tty_fd)
        if owner is None:
            return
        key = hashlib.sha256(f"{terminal}:{owner}".encode()).hexdigest()
        STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_fd = os.open(
            STATE_DIR / (key + ".state"),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            if not stat.S_ISREG(os.fstat(state_fd).st_mode):
                return
            fcntl.flock(state_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = os.read(state_fd, 16384)
            previous = json.loads(raw) if raw else None
            record = transition(previous, event, payload)
            if record is None:
                return
            record.update(terminal=terminal, owner=owner)
            emit(tty_fd, record)
            os.lseek(state_fd, 0, os.SEEK_SET)
            os.write(state_fd, json.dumps(record).encode())
            os.ftruncate(state_fd, os.lseek(state_fd, 0, os.SEEK_CUR))
        finally:
            os.close(state_fd)
    finally:
        os.close(tty_fd)


def main():
    # Never return hook output/decisions. Reporting failures must not become
    # permission decisions, model context, or an unbounded wait.
    signal.signal(signal.SIGALRM, lambda *_: os._exit(0))
    signal.setitimer(signal.ITIMER_REAL, 0.4)
    try:
        payload = json.loads(sys.stdin.buffer.read(1024 * 1024))
        report(sys.argv[1], payload)
    except Exception:
        pass


if __name__ == "__main__":
    main()
