import os
import shlex
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris.util.xdgshortcuts import (
    create_launcher,
    desktop_launcher_exists,
    get_lutris_executable,
    get_lutris_try_exec,
)


def _parse_desktop(path: str) -> dict[str, str]:
    entries = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line or line.startswith("[") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            entries[key] = value
    return entries


def _touch_executable(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("#!/usr/bin/env python3\n")
    os.chmod(path, 0o755)


class TestLutrisCommand(TestCase):
    def test_source_tree_uses_interpreter_and_script(self):
        with TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "bin", "lutris")
            python = os.path.join(tmp, "python3")
            _touch_executable(script)
            _touch_executable(python)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=None),
                patch("lutris.util.xdgshortcuts.sys.argv", [script]),
                patch("lutris.util.xdgshortcuts.sys.executable", python),
            ):
                linux_system.is_flatpak.return_value = False
                command = get_lutris_executable()
                self.assertEqual(command, f"{shlex.quote(python)} {shlex.quote(script)}")
                self.assertEqual(get_lutris_try_exec(), script)

    def test_packaged_lutris_on_path_uses_bare_name(self):
        with TemporaryDirectory() as tmp:
            lutris_bin = os.path.join(tmp, "lutris")
            _touch_executable(lutris_bin)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=lutris_bin),
                patch("lutris.util.xdgshortcuts.sys.argv", [lutris_bin]),
            ):
                linux_system.is_flatpak.return_value = False
                self.assertEqual(get_lutris_executable(), "lutris")
                self.assertEqual(get_lutris_try_exec(), "lutris")

    def test_prefers_running_source_tree_over_packaged_lutris(self):
        with TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "bin", "lutris")
            packaged = os.path.join(tmp, "usr", "bin", "lutris")
            python = os.path.join(tmp, "python3")
            _touch_executable(script)
            _touch_executable(packaged)
            _touch_executable(python)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=packaged),
                patch("lutris.util.xdgshortcuts.sys.argv", [script]),
                patch("lutris.util.xdgshortcuts.sys.executable", python),
            ):
                linux_system.is_flatpak.return_value = False
                self.assertEqual(get_lutris_executable(), f"{shlex.quote(python)} {shlex.quote(script)}")
                self.assertEqual(get_lutris_try_exec(), script)

    def test_flatpak_uses_flatpak_run(self):
        with (
            patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
            patch("lutris.util.xdgshortcuts.shutil.which", return_value=None),
        ):
            linux_system.is_flatpak.return_value = True
            self.assertEqual(get_lutris_executable(), "flatpak run net.lutris.Lutris")
            self.assertIsNone(get_lutris_try_exec())

    def test_omits_tryexec_when_lutris_cannot_be_resolved(self):
        with (
            patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
            patch("lutris.util.xdgshortcuts.shutil.which", return_value=None),
            patch("lutris.util.xdgshortcuts.sys.argv", ["lutris"]),
        ):
            linux_system.is_flatpak.return_value = False
            self.assertEqual(get_lutris_executable(), "lutris")
            self.assertIsNone(get_lutris_try_exec())


class TestCreateLauncher(TestCase):
    def test_writes_executable_shortcuts_for_a_source_install(self):
        with TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "bin", "lutris")
            python = os.path.join(tmp, "python3")
            desktop_dir = os.path.join(tmp, "Desktop")
            data_dir = os.path.join(tmp, "share")
            _touch_executable(script)
            _touch_executable(python)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=None),
                patch("lutris.util.xdgshortcuts.sys.argv", [script]),
                patch("lutris.util.xdgshortcuts.sys.executable", python),
                patch("lutris.util.xdgshortcuts.GLib.get_user_special_dir", return_value=desktop_dir),
                patch("lutris.util.xdgshortcuts.GLib.get_user_data_dir", return_value=data_dir),
            ):
                linux_system.is_flatpak.return_value = False
                create_launcher("coolgame", "7", "Cool Game {GOTY}", desktop=True, menu=True)

            desktop_path = os.path.join(desktop_dir, "net.lutris.coolgame-7.desktop")
            menu_path = os.path.join(data_dir, "applications", "net.lutris.coolgame-7.desktop")
            self.assertTrue(os.path.isfile(desktop_path))
            self.assertTrue(os.path.isfile(menu_path))
            self.assertTrue(os.access(desktop_path, os.X_OK))
            self.assertTrue(os.access(menu_path, os.X_OK))

            entries = _parse_desktop(desktop_path)
            self.assertEqual(entries["Name"], "Cool Game {GOTY}")
            self.assertEqual(entries["Icon"], "lutris_coolgame")
            self.assertEqual(entries["TryExec"], script)
            self.assertIn(python, entries["Exec"])
            self.assertIn(script, entries["Exec"])
            self.assertIn("LUTRIS_SKIP_INIT=1", entries["Exec"])
            self.assertIn("lutris:rungameid/7", entries["Exec"])
            self.assertEqual(_parse_desktop(menu_path)["TryExec"], script)

    def test_packaged_install_writes_tryexec_lutris(self):
        with TemporaryDirectory() as tmp:
            lutris_bin = os.path.join(tmp, "lutris")
            desktop_dir = os.path.join(tmp, "Desktop")
            _touch_executable(lutris_bin)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=lutris_bin),
                patch("lutris.util.xdgshortcuts.sys.argv", [lutris_bin]),
                patch("lutris.util.xdgshortcuts.GLib.get_user_special_dir", return_value=desktop_dir),
            ):
                linux_system.is_flatpak.return_value = False
                create_launcher("doom", "1", "DOOM", desktop=True)

            entries = _parse_desktop(os.path.join(desktop_dir, "net.lutris.doom-1.desktop"))
            self.assertEqual(entries["TryExec"], "lutris")
            self.assertIn("lutris lutris:rungameid/1", entries["Exec"])

    def test_menu_shortcut_without_desktop_directory(self):
        with TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "bin", "lutris")
            python = os.path.join(tmp, "python3")
            data_dir = os.path.join(tmp, "share")
            _touch_executable(script)
            _touch_executable(python)
            with (
                patch("lutris.util.xdgshortcuts.LINUX_SYSTEM") as linux_system,
                patch("lutris.util.xdgshortcuts.shutil.which", return_value=None),
                patch("lutris.util.xdgshortcuts.sys.argv", [script]),
                patch("lutris.util.xdgshortcuts.sys.executable", python),
                patch("lutris.util.xdgshortcuts.GLib.get_user_special_dir", return_value=None),
                patch("lutris.util.xdgshortcuts.GLib.get_user_data_dir", return_value=data_dir),
            ):
                linux_system.is_flatpak.return_value = False
                create_launcher("doom", "1", "DOOM", desktop=True, menu=True)

            self.assertTrue(os.path.isfile(os.path.join(data_dir, "applications", "net.lutris.doom-1.desktop")))


class TestDesktopLauncherExists(TestCase):
    def test_missing_desktop_directory_is_not_an_error(self):
        with patch("lutris.util.xdgshortcuts.GLib.get_user_special_dir", return_value=None):
            self.assertFalse(desktop_launcher_exists("doom", "1"))
