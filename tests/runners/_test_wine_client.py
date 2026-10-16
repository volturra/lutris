"""Tests for the Wine runner following games launched through a client such as Battle.net."""

from unittest import TestCase
from unittest.mock import MagicMock, PropertyMock, patch

from lutris.util.test_config import setup_test_environment

setup_test_environment()

from lutris.runners import wine  # noqa: E402  (must follow setup_test_environment)

BATTLENET_EXE = "/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"


def make_tracker(alive=False):
    tracker = MagicMock()
    tracker.is_alive.return_value = alive
    return tracker


class ClientKeepGameAliveTest(TestCase):
    def setUp(self):
        self.runner = wine.wine()
        wine.BATTLENET_GAME_TRACKERS.clear()
        self.addCleanup(wine.BATTLENET_GAME_TRACKERS.clear)

    def test_tracking_starts_only_for_battlenet(self):
        cases = ((BATTLENET_EXE, "/pfx", True), ("/pfx/drive_c/Other/client.exe", "/pfx", False), (None, "/pfx", False))
        cases += ((BATTLENET_EXE, None, False),)
        for client_exe, prefix, expected in cases:
            with (
                self.subTest(client_exe=client_exe, prefix=prefix),
                patch.object(
                    wine.wine, "game_config", new_callable=PropertyMock, return_value={"client_exe": client_exe}
                ),
                patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value=prefix),
                patch.object(wine, "BattleNetGameTracker") as tracker_class,
            ):
                wine.BATTLENET_GAME_TRACKERS[""] = make_tracker()
                self.runner._start_client_game_tracking()
                self.assertEqual(wine.BATTLENET_GAME_TRACKERS.get("") is tracker_class.return_value, expected)
                self.assertEqual("" in wine.BATTLENET_GAME_TRACKERS, expected)

    def test_no_client_uses_normal_monitoring(self):
        self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))

    def test_alive_while_launch_command_runs(self):
        wine.BATTLENET_GAME_TRACKERS[""] = make_tracker()
        self.assertTrue(self.runner.keep_game_alive(set(), game_thread_running=True))

    def test_alive_while_client_runs_the_game(self):
        wine.BATTLENET_GAME_TRACKERS[""] = make_tracker(alive=True)
        self.assertTrue(self.runner.keep_game_alive(set(), game_thread_running=False))

    def test_tracking_survives_a_new_runner(self):
        """Reloading a game's configuration rebuilds its runner while the game runs."""
        wine.BATTLENET_GAME_TRACKERS[""] = make_tracker(alive=True)
        self.assertTrue(wine.wine().keep_game_alive(set(), game_thread_running=False))

    def test_quits_and_forgets_the_game_when_it_closes(self):
        wine.BATTLENET_GAME_TRACKERS[""] = make_tracker(alive=False)
        self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))
        self.assertNotIn("", wine.BATTLENET_GAME_TRACKERS)

    def test_force_stop_forgets_the_game(self):
        wine.BATTLENET_GAME_TRACKERS[""] = make_tracker(alive=True)
        with (
            patch.object(wine, "winekill"),
            patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value="/pfx"),
            patch.object(wine.wine, "get_executable", return_value="wine"),
            patch.object(wine.wine, "get_env", return_value={}),
            patch.object(wine.Runner, "force_stop_game"),
        ):
            self.runner.force_stop_game(set())
        self.assertFalse(self.runner.keep_game_alive(set(), game_thread_running=False))
