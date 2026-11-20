"""Tests for following Battle.net's own log."""

import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris.util.battlenet import client_log
from lutris.util.battlenet.client_log import (
    BattleNetGameTracker,
    BattleNetLogWatcher,
    get_exe_name,
    get_log_dirs,
    is_battlenet_client,
    is_prefix_process_running,
)

CLIENT_READY = (
    "I 2026-10-15 20:16:52.452920 [GameSessionList] {Agent} Reporting process: "
    "TrackingInfo(uid=battle.net, type=game, opStatus=On, reqId=0, running=4, oldRunning=0)"
)
LAUNCH_WOW = "I 2026-10-15 20:19:30.349541 [GameLaunchController] {Main} LaunchBinary: uid=wow selectedRegion=US"
WOW_LAUNCHED_EXE = (
    "I 2026-10-15 20:19:30.927653 [InstallManager] {Main} Launched "
    "C:/Program Files (x86)/World of Warcraft/_retail_/WoW.exe with args: -launcherlogin -uid wow, pid: 2068"
)
LAUNCH_DIABLO = "I 2026-10-15 20:25:00.000000 [GameLaunchController] {Main} LaunchBinary: uid=fenris selectedRegion=US"
WOW_RUNNING = (
    "I 2026-10-15 20:19:31.491428 [GameSessionList] {Agent} Reporting process: "
    "TrackingInfo(uid=wow, type=game, opStatus=On, reqId=0, running=1, oldRunning=0)"
)
WOW_CLOSED = (
    "I 2026-10-15 20:20:07.260694 [GameSessionList] {Agent} Reporting process: "
    "TrackingInfo(uid=wow, type=game, opStatus=Off, reqId=0, running=0, oldRunning=1)"
)
ANOTHER_INSTANCE = (
    "I 2026-10-15 20:19:30.347158 [Main] {Main} Leaving because another instance of battle.net is running"
)


class FakePrefixTestCase(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.prefix = directory.name
        users_dir = os.path.join(self.prefix, "drive_c", "users")
        self.log_dir = os.path.join(users_dir, "steamuser", "AppData", "Local", "Battle.net", "Logs")
        os.makedirs(self.log_dir)

    def write_log(self, name, *lines, partial=""):
        with open(os.path.join(self.log_dir, name), "a", encoding="utf-8") as log_file:
            for line in lines:
                log_file.write(line + "\n")
            log_file.write(partial)


class BattleNetLogWatcherTest(FakePrefixTestCase):
    def test_is_battlenet_client(self):
        self.assertTrue(is_battlenet_client("/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"))
        self.assertTrue(is_battlenet_client("C:\\Program Files (x86)\\Battle.net\\Battle.net Launcher.exe"))
        self.assertFalse(is_battlenet_client("/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Wow.exe"))

    def test_linked_user_folders_are_listed_once(self):
        os.symlink("steamuser", os.path.join(self.prefix, "drive_c", "users", "someone"))
        self.assertEqual(get_log_dirs(self.prefix), [os.path.realpath(self.log_dir)])

    def test_ignores_lines_written_before_the_watcher(self):
        self.write_log("battle.net-1.log", CLIENT_READY, LAUNCH_WOW, WOW_RUNNING)
        watcher = BattleNetLogWatcher(self.prefix)
        watcher.poll()
        self.assertFalse(watcher.client_ready)
        self.assertFalse(watcher.game_running)

    def test_client_ready_from_agent_report(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", "I something else")
        watcher.poll()
        self.assertFalse(watcher.client_ready)
        self.write_log("battle.net-1.log", CLIENT_READY)
        watcher.poll()
        self.assertTrue(watcher.client_ready)

    def test_client_ready_when_another_instance_runs(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-2.log", ANOTHER_INSTANCE)
        watcher.poll()
        self.assertTrue(watcher.client_ready)

    def test_follows_launched_game_until_it_closes(self):
        self.write_log("battle.net-1.log", CLIENT_READY)
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", LAUNCH_WOW)
        watcher.poll()
        self.assertEqual(watcher.launched_uid, "wow")
        self.assertFalse(watcher.game_running)
        self.write_log("battle.net-1.log", WOW_RUNNING)
        watcher.poll()
        self.assertTrue(watcher.game_running)
        self.write_log("battle.net-1.log", WOW_CLOSED)
        watcher.poll()
        self.assertFalse(watcher.game_running)

    def test_game_not_launched_by_this_watcher_is_ignored(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", WOW_RUNNING)
        watcher.poll()
        self.assertFalse(watcher.game_running)

    def test_partial_line_is_read_once_complete(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", partial=CLIENT_READY[:40])
        watcher.poll()
        self.assertFalse(watcher.client_ready)
        self.write_log("battle.net-1.log", CLIENT_READY[40:])
        watcher.poll()
        self.assertTrue(watcher.client_ready)

    def test_only_the_first_launch_is_followed(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", LAUNCH_WOW, WOW_LAUNCHED_EXE, WOW_RUNNING, LAUNCH_DIABLO)
        watcher.poll()
        self.assertEqual(watcher.launched_uid, "wow")
        self.assertEqual(watcher.launched_exe, "C:/Program Files (x86)/World of Warcraft/_retail_/WoW.exe")
        self.assertTrue(watcher.game_running)

    def test_missing_prefix_has_no_logs(self):
        self.assertEqual(get_log_dirs(None), [])
        watcher = BattleNetLogWatcher(None)
        watcher.poll()
        self.assertFalse(watcher.has_new_lines)


class WaitUntilReadyTest(FakePrefixTestCase):
    def test_returns_once_ready(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", CLIENT_READY)
        with patch.object(client_log.time, "sleep") as sleep:
            watcher.wait_until_ready(quiet_timeout=15)
        self.assertTrue(watcher.client_ready)
        sleep.assert_called_once_with(client_log.READY_SETTLE_SECONDS)

    def test_gives_up_early_when_the_log_stays_quiet(self):
        watcher = BattleNetLogWatcher(self.prefix)
        clock = iter([0, 5, 16])
        with (
            patch.object(client_log.time, "monotonic", side_effect=lambda: next(clock)),
            patch.object(client_log.time, "sleep") as sleep,
        ):
            watcher.wait_until_ready(quiet_timeout=15)
        self.assertFalse(watcher.client_ready)
        self.assertEqual(sleep.call_count, 1)

    def test_keeps_waiting_while_the_client_writes_its_log(self):
        watcher = BattleNetLogWatcher(self.prefix)
        self.write_log("battle.net-1.log", "I starting up")
        clock = iter([0, 16, client_log.READY_TIMEOUT_SECONDS])
        with (
            patch.object(client_log.time, "monotonic", side_effect=lambda: next(clock)),
            patch.object(client_log.time, "sleep") as sleep,
        ):
            watcher.wait_until_ready(quiet_timeout=15)
        self.assertEqual(sleep.call_count, 1)


class BattleNetGameTrackerTest(FakePrefixTestCase):
    def setUp(self):
        super().setUp()
        self.running_processes = {"battle.net.exe", "wow.exe"}
        self.tracker = BattleNetGameTracker(self.prefix, lambda names: bool(self.running_processes & set(names)))

    def test_follows_the_game_until_battlenet_reports_it_closed(self):
        self.write_log("battle.net-1.log", LAUNCH_WOW, WOW_LAUNCHED_EXE, WOW_RUNNING)
        self.assertTrue(self.tracker.is_alive())
        self.write_log("battle.net-1.log", WOW_CLOSED)
        self.assertFalse(self.tracker.is_alive())

    def test_game_started_by_a_stub_is_followed_while_the_client_runs(self):
        self.write_log("battle.net-1.log", LAUNCH_WOW, WOW_LAUNCHED_EXE, WOW_RUNNING)
        self.running_processes.discard("wow.exe")
        self.assertTrue(self.tracker.is_alive())

    def test_game_is_closed_when_its_processes_are_gone(self):
        """Killing the prefix stops Battle.net before it can log that the game closed."""
        self.write_log("battle.net-1.log", LAUNCH_WOW, WOW_LAUNCHED_EXE, WOW_RUNNING)
        self.assertTrue(self.tracker.is_alive())
        self.running_processes.clear()
        self.assertFalse(self.tracker.is_alive())

    def test_waits_for_the_game_to_start_then_gives_up(self):
        with patch.object(client_log.time, "monotonic", return_value=0):
            self.assertTrue(self.tracker.is_alive())
        with patch.object(client_log.time, "monotonic", return_value=client_log.GAME_LAUNCH_GRACE_SECONDS + 1):
            self.assertFalse(self.tracker.is_alive())


class PrefixProcessTest(TestCase):
    def test_finds_processes_by_file_name(self):
        with open("/proc/self/cmdline", "rb") as cmdline_file:
            own_exe = get_exe_name(cmdline_file.read().split(b"\0")[0].decode())
        # The test runner has no WINEPREFIX, which counts as being in any prefix
        self.assertTrue(is_prefix_process_running("/pfx", [own_exe]))
        self.assertFalse(is_prefix_process_running("/pfx", ["no-such-program.exe"]))
