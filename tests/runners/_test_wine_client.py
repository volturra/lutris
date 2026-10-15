"""Tests for the Wine runner following games launched through a client such as Battle.net."""

from unittest import TestCase
from unittest.mock import MagicMock, PropertyMock, patch

from lutris.util.test_config import setup_test_environment

setup_test_environment()

from lutris.runners import wine  # noqa: E402  (must follow setup_test_environment)


def make_watcher(game_running=False):
    watcher = MagicMock()
    watcher.game_running = game_running
    return watcher


class ClientKeepGameAliveTest(TestCase):
    def setUp(self):
        self.runner = wine.wine()

    def test_tracking_starts_only_for_battlenet(self):
        bnet = "/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"
        for client_exe, expected in ((bnet, True), ("/pfx/drive_c/Other/client.exe", False), (None, False)):
            with (
                patch.object(
                    wine.wine, "game_config", new_callable=PropertyMock, return_value={"client_exe": client_exe}
                ),
                patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value="/pfx"),
                patch.object(wine, "BattleNetLogWatcher") as watcher_class,
            ):
                self.runner._client_game_seen = True
                self.runner._start_client_game_tracking()
                self.assertEqual(self.runner._client_log_watcher is not None, expected)
                self.assertEqual(watcher_class.called, expected)
                self.assertFalse(self.runner._client_game_seen)

    def test_no_client_uses_normal_monitoring(self):
        self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))

    def test_alive_while_launch_command_runs(self):
        self.runner._client_log_watcher = make_watcher()
        self.assertTrue(self.runner.keep_game_alive(set(), game_thread_running=True))

    def test_alive_while_client_runs_the_game(self):
        self.runner._client_log_watcher = make_watcher(game_running=True)
        self.assertTrue(self.runner.keep_game_alive(set(), game_thread_running=False))
        self.assertTrue(self.runner._client_game_seen)

    def test_quits_when_client_reports_game_closed(self):
        watcher = make_watcher(game_running=True)
        self.runner._client_log_watcher = watcher
        self.runner.keep_game_alive(set(), game_thread_running=False)
        watcher.game_running = False
        self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))

    def test_waits_for_the_game_to_start(self):
        self.runner._client_log_watcher = make_watcher()
        self.assertTrue(self.runner.keep_game_alive(set(), game_thread_running=False))

    def test_gives_up_after_grace_period(self):
        self.runner._client_log_watcher = make_watcher()
        self.runner._client_launch_start = 0.0
        with patch.object(wine.time, "monotonic", return_value=wine.CLIENT_GAME_LAUNCH_GRACE_SECONDS + 1):
            self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))


class WaitForBattleNetTest(TestCase):
    def test_returns_once_ready(self):
        watcher = MagicMock()
        watcher.client_ready = False

        def poll():
            watcher.client_ready = True

        watcher.poll.side_effect = poll
        with patch.object(wine.time, "sleep") as sleep:
            wine.wine._wait_for_battlenet_ready(watcher)
        sleep.assert_called_once_with(wine.CLIENT_READY_SETTLE_SECONDS)

    def test_gives_up_after_timeout(self):
        watcher = MagicMock()
        watcher.client_ready = False
        clock = iter([0, wine.CLIENT_READY_TIMEOUT_SECONDS + 1])
        with (
            patch.object(wine.time, "monotonic", side_effect=lambda: next(clock)),
            patch.object(wine.time, "sleep") as sleep,
        ):
            wine.wine._wait_for_battlenet_ready(watcher)
        sleep.assert_not_called()
