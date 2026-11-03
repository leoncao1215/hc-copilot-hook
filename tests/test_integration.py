import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest

if sys.platform == "win32":
    raise unittest.SkipTest("macOS PTY tests; Windows has its own offline adapter tests")

import pty
import select
import subprocess
import tempfile
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("bridge")
startup = load("startup")
installer = load("install")


def payload(session="root", timestamp=100, **extra):
    return {"sessionId": session, "timestamp": timestamp, **extra}


class BridgeTests(unittest.TestCase):
    def test_lifecycle_and_actual_prompt_notifications(self):
        state = bridge.transition(None, "sessionStart", payload())
        self.assertEqual(state["state"], "idle")
        state = bridge.transition(state, "userPromptSubmitted", payload(timestamp=101))
        self.assertEqual(state["state"], "working")
        for notification, kind in (
            ("permission_prompt", "permission"), ("elicitation_dialog", "question")
        ):
            blocked = bridge.transition(
                state, "notification",
                payload(timestamp=102, notification_type=notification),
            )
            self.assertEqual((blocked["state"], blocked["kind"]), ("blocked", kind))
            working = bridge.transition(blocked, "postToolUse", payload(timestamp=103))
            self.assertEqual(working["state"], "working")
        done = bridge.transition(
            state, "agentStop", payload(timestamp=104, stopReason="end_turn")
        )
        self.assertEqual(done["state"], "done")

    def test_children_and_background_notifications_cannot_change_root(self):
        root = bridge.transition(None, "sessionStart", payload())
        for field in bridge.CHILD_FIELDS:
            for event in ("sessionStart", "agentStop", "notification", "postToolUse"):
                with self.subTest(field=field, event=event):
                    self.assertIsNone(bridge.transition(
                        root, event,
                        payload(timestamp=101, stopReason="end_turn",
                                notification_type="permission_prompt", **{field: "child"}),
                    ))
        for event in ("agent_completed", "agent_idle", "shell_completed"):
            self.assertIsNone(bridge.transition(
                root, "notification", payload(timestamp=101, notification_type=event)
            ))

    def test_late_events_and_other_sessions_are_ignored(self):
        root = bridge.transition(None, "sessionStart", payload(timestamp=200))
        self.assertIsNone(bridge.transition(
            root, "notification", payload(timestamp=199, notification_type="permission_prompt")
        ))
        newer = bridge.transition(root, "sessionStart", payload("new-root", 201))
        self.assertIsNone(bridge.transition(newer, "sessionEnd", payload(timestamp=202)))
        self.assertIsNone(bridge.transition(
            None, "agentStop", payload(timestamp=203, stopReason="end_turn")
        ))

    def test_only_foreground_copilot_ancestor_is_allowed(self):
        processes = subprocess.CompletedProcess(
            [], 0,
            "100 1 /bin/zsh\n200 100 /Users/example/copilot\n"
            "210 200 /bin/bash\n220 210 /Users/example/copilot\n230 220 /bin/bash\n",
        )
        with patch.object(bridge.os, "tcgetpgrp", return_value=200), \
                patch.object(bridge.subprocess, "run", return_value=processes):
            with patch.object(bridge.os, "getppid", return_value=210):
                self.assertEqual(bridge.foreground_copilot(9), 200)
            with patch.object(bridge.os, "getppid", return_value=230):
                self.assertIsNone(bridge.foreground_copilot(9))

    def test_two_ptys_do_not_share_state_or_output(self):
        real_open = os.open
        masters, slaves = [], []
        with tempfile.TemporaryDirectory() as directory:
            try:
                for _ in range(2):
                    master, slave = pty.openpty()
                    masters.append(master)
                    slaves.append(slave)

                def run(index, event, data):
                    def open_file(path, flags, *args, **kwargs):
                        if path == "/dev/tty":
                            return os.dup(slaves[index])
                        return real_open(path, flags, *args, **kwargs)
                    with patch.object(bridge, "STATE_DIR", Path(directory)), \
                            patch.object(bridge, "foreground_copilot", return_value=123), \
                            patch.object(bridge.os, "open", side_effect=open_file), \
                            patch.dict(os.environ, {"HC_TERMINAL_ID": f"t{index}"}):
                        bridge.report(event, data)

                for index in range(2):
                    run(index, "sessionStart", payload(f"session-{index}"))
                    self.assertIn(b"state=idle", os.read(masters[index], 1024))
                run(0, "notification", payload(
                    "session-0", 101, notification_type="elicitation_dialog"
                ))
                self.assertIn(b"state=blocked", os.read(masters[0], 1024))
                self.assertEqual(select.select([masters[1]], [], [], 0)[0], [])
                run(1, "agentStop", payload("session-0", 102, stopReason="end_turn"))
                self.assertEqual(select.select(masters, [], [], 0)[0], [])
                states = [json.loads(p.read_text()) for p in Path(directory).glob("*.state")]
                self.assertEqual(
                    {s["terminal"]: s["state"] for s in states},
                    {"t0": "blocked", "t1": "idle"},
                )
            finally:
                for fd in masters + slaves:
                    os.close(fd)

    def test_failures_are_silent_and_successful(self):
        env = dict(os.environ, HC_TERMINAL_ID="test")
        for data in ("invalid JSON", json.dumps(payload()), "[]"):
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "bridge.py"), "sessionStart"],
                input=data, capture_output=True, text=True, env=env,
                start_new_session=True, timeout=2,
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_stalled_input_is_bounded(self):
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-I", str(ROOT / "bridge.py"), "sessionStart"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(process.wait(timeout=2), 0)
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertEqual(process.stdout.read(), b"")
            self.assertEqual(process.stderr.read(), b"")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            if process.poll() is None:
                process.kill()
                process.wait()


class StartupTests(unittest.TestCase):
    def test_interactive_modes_and_excluded_invocations(self):
        for args in (
            [], ["--resume"], ["--resume=session"], ["--resume", "session"],
            ["-r", "session"], ["--continue"], ["--model", "model", "--resume", "session"],
            ["-C", "/some project", "--resume=session"], ["-i", "help"],
        ):
            with self.subTest(args=args):
                self.assertTrue(startup.interactive_args(args))
        for args in (
            ["--help"], ["version"], ["--version"], ["-p", "hello"],
            ["--prompt=hello"], ["-phello"], ["--acp"], ["--resume", "s", "--acp"],
            ["--output-format=json"], ["--host-environment", "test"],
            ["--unknown-option"], ["--model"], ["--resume", "s", "--prompt", "hello"],
            ["--model", "m", "sessions", "list"],
        ):
            with self.subTest(args=args):
                self.assertFalse(startup.interactive_args(args))

    def test_background_and_nested_processes_are_rejected(self):
        processes = subprocess.CompletedProcess(
            [], 0,
            "100 1 /bin/zsh\n200 100 /Users/example/copilot\n210 200 /bin/zsh\n",
        )
        with patch.object(startup.subprocess, "run", return_value=processes), \
                patch.object(startup.os, "tcgetpgrp", return_value=123), \
                patch.object(startup.os, "getpgrp", return_value=123):
            with patch.object(startup.os, "getppid", return_value=100):
                self.assertTrue(startup.allowed_process(9))
            with patch.object(startup.os, "getppid", return_value=210):
                self.assertFalse(startup.allowed_process(9))
            with patch.object(startup.os, "getpgrp", return_value=456):
                self.assertFalse(startup.allowed_process(9))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hc-hook space's-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "copilot home"
        self.home.mkdir()
        self.zshrc = self.root / ".zshrc"
        self.original = b"# existing shell settings\nexport EXAMPLE=preserved\n"
        self.zshrc.write_bytes(self.original)
        self.zshrc.chmod(0o640)
        (self.home / "hooks").mkdir()
        (self.home / "hooks/other.json").write_text('{"version":1,"hooks":{}}\n')
        (self.home / "settings.json").write_text('{"defaultPermissionMode":"manual"}\n')

    def install(self, zsh=True):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.home, self.zshrc if zsh else None)

    def uninstall(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall(self.home)

    def test_install_update_uninstall_preserve_user_edits_and_backups(self):
        self.install()
        self.install()
        self.assertEqual(self.zshrc.read_text().count(installer.BEGIN), 1)
        self.assertEqual(self.zshrc.stat().st_mode & 0o777, 0o640)
        backup_contents = [
            p.read_bytes() for p in (self.home / "backups").glob("*/[0-9]*-.zshrc")
        ]
        self.assertIn(self.original, backup_contents)
        config = json.loads((self.home / "hooks/herdr-app-status.json").read_text())
        self.assertNotIn("preToolUse", config["hooks"])
        self.assertNotIn("permissionRequest", config["hooks"])
        self.assertTrue(all(
            hook["timeoutSec"] == 1 and hook["bash"].endswith(">/dev/null 2>&1 || true")
            for entries in config["hooks"].values() for hook in entries
        ))
        self.zshrc.write_text(self.zshrc.read_text() + "export LATER_EDIT=keep\n")
        self.uninstall()
        self.assertEqual(self.zshrc.read_bytes(), self.original + b"export LATER_EDIT=keep\n")
        self.assertFalse((self.home / "hooks/herdr-app-status.json").exists())
        self.assertEqual(
            (self.home / "settings.json").read_text(),
            '{"defaultPermissionMode":"manual"}\n',
        )
        self.assertTrue((self.home / "hooks/other.json").exists())

    def test_hooks_only_can_add_shell_wrapper_later(self):
        self.install(zsh=False)
        self.assertEqual(self.zshrc.read_bytes(), self.original)
        self.install()
        self.assertIn(installer.BEGIN, self.zshrc.read_text())
        self.uninstall()
        self.assertEqual(self.zshrc.read_bytes(), self.original)

    def test_uninstall_refuses_to_delete_modified_owned_files(self):
        self.install()
        hook = self.home / "hooks/herdr-app-status.json"
        hook.write_text(hook.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "Local edits"):
            self.uninstall()
        self.assertTrue(hook.exists())
        self.assertTrue((self.home / "herdr-app-status/bridge.py").exists())

    def test_unmanaged_collision_or_symlink_changes_nothing(self):
        hook = self.home / "hooks/herdr-app-status.json"
        hook.write_text('{"existing":"keep"}\n')
        with self.assertRaisesRegex(ValueError, "Unmanaged file"):
            self.install()
        self.assertEqual(hook.read_text(), '{"existing":"keep"}\n')
        self.assertFalse((self.home / "herdr-app-status").exists())
        hook.unlink()
        self.zshrc.unlink()
        target = self.root / "other"
        target.write_bytes(self.original)
        self.zshrc.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.install()
        self.assertEqual(target.read_bytes(), self.original)
        self.assertFalse(hook.exists())

    def test_rendered_wrapper_preserves_arguments_output_and_exit(self):
        self.install()
        wrapper = self.home / "herdr-app-status/copilot.zsh"
        subprocess.run(["/bin/zsh", "-n", str(wrapper)], check=True, timeout=2)
        subprocess.run(["/bin/zsh", "-n", str(self.zshrc)], check=True, timeout=2)
        executable = self.root / "copilot"
        executable.write_text("#!/bin/sh\nprintf '<%s>\\n' \"$@\"\nexit 17\n")
        executable.chmod(0o700)
        env = dict(os.environ, PATH=str(self.root) + ":" + os.environ["PATH"])
        env.pop("HC_TERMINAL_ID", None)
        result = subprocess.run(
            ["/bin/zsh", "-f", "-c", 'source "$1"; shift; copilot "$@"',
             "test", str(wrapper), "--resume", "session with spaces", "literal;not-a-command"],
            env=env, capture_output=True, text=True, timeout=2,
        )
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout,
                         "<--resume>\n<session with spaces>\n<literal;not-a-command>\n")
        self.assertEqual(result.stderr, "")
        # The generated hook command must also survive spaces and quotes in paths.
        config = json.loads((self.home / "hooks/herdr-app-status.json").read_text())
        command = config["hooks"]["sessionStart"][0]["bash"]
        result = subprocess.run(
            ["/bin/bash", "-c", command], input="bad-json", capture_output=True,
            text=True, env=env, start_new_session=True, timeout=2,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))


if __name__ == "__main__":
    unittest.main()
