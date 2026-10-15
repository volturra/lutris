"""Tests for following Battle.net's own log."""

import os
import tempfile
from unittest import TestCase

from lutris.util.battlenet.client_log import BattleNetLogWatcher, get_log_dirs, is_battlenet_client

CLIENT_READY = (
    "I 2026-10-15 20:16:52.452920 [GameSessionList] {Agent} Reporting process: "
    "TrackingInfo(uid=battle.net, type=game, opStatus=On, reqId=0, running=4, oldRunning=0)"
)
LAUNCH_WOW = "I 2026-10-15 20:19:30.349541 [GameLaunchController] {Main} LaunchBinary: uid=wow selectedRegion=US"
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


class BattleNetLogWatcherTest(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.prefix = self.tempdir.name
        users_dir = os.path.join(self.prefix, "drive_c", "users")
        self.log_dir = os.path.join(users_dir, "steamuser", "AppData", "Local", "Battle.net", "Logs")
        os.makedirs(self.log_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_log(self, name, *lines, partial=""):
        with open(os.path.join(self.log_dir, name), "a", encoding="utf-8") as log_file:
            for line in lines:
                log_file.write(line + "\n")
            log_file.write(partial)

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
