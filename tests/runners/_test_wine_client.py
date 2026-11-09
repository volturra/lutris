"""Tests for the Wine runner following games launched through a client such as Battle.net."""

from unittest import TestCase
from unittest.mock import MagicMock, PropertyMock, patch

from lutris.util.test_config import setup_test_environment

setup_test_environment()

from lutris.runners import wine  # noqa: E402  (must follow setup_test_environment)
from lutris.util.battlenet import client_log  # noqa: E402
from lutris.util.battlenet.client_log import BattleNetGameTracker  # noqa: E402

BATTLENET_EXE = "/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net.exe"


def make_runner(game_config_id="game-1", client_exe=BATTLENET_EXE):
    """A wine runner whose config is stubbed, so no real config, database or disk is touched."""
    runner = wine.wine()
    runner._config = MagicMock(game_config_id=game_config_id, game_config={"client_exe": client_exe})
    return runner


def track(game_config_id="game-1", alive=False):
    tracker = MagicMock(spec=BattleNetGameTracker)
    tracker.is_alive.return_value = alive
    client_log._game_trackers[game_config_id] = tracker
    return tracker


class ClientKeepGameAliveTest(TestCase):
    def setUp(self):
        client_log._game_trackers.clear()
        self.addCleanup(client_log._game_trackers.clear)

    def test_tracking_starts_only_for_battlenet(self):
        cases = (
            ("game-1", BATTLENET_EXE, "/pfx", True),
            ("game-1", "/pfx/drive_c/Other/client.exe", "/pfx", False),
            ("game-1", None, "/pfx", False),
            ("game-1", BATTLENET_EXE, None, False),
            (None, BATTLENET_EXE, "/pfx", False),
        )
        for game_config_id, client_exe, prefix, expected in cases:
            with (
                self.subTest(game_config_id=game_config_id, client_exe=client_exe, prefix=prefix),
                patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value=prefix),
            ):
                client_log._game_trackers.clear()
                make_runner(game_config_id, client_exe)._start_client_game_tracking()
                self.assertEqual(client_log.get_game_tracker(game_config_id) is not None, expected)

    def test_new_launch_replaces_the_previous_tracking(self):
        stale_tracker = track()
        with patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value="/pfx"):
            make_runner()._start_client_game_tracking()
        self.assertIsNot(client_log.get_game_tracker("game-1"), stale_tracker)

    def test_no_client_uses_normal_monitoring(self):
        self.assertFalse(make_runner().keep_game_alive(set(), game_thread_running=False))

    def test_alive_while_launch_command_runs(self):
        track()
        self.assertTrue(make_runner().keep_game_alive(set(), game_thread_running=True))

    def test_alive_while_client_runs_the_game(self):
        track(alive=True)
        self.assertTrue(make_runner().keep_game_alive(set(), game_thread_running=False))

    def test_tracking_survives_a_new_runner_and_is_kept_per_game(self):
        """Reloading a game's configuration rebuilds its runner while the game runs."""
        track("game-1", alive=True)
        self.assertTrue(make_runner("game-1").keep_game_alive(set(), game_thread_running=False))
        self.assertFalse(make_runner("game-2").keep_game_alive(set(), game_thread_running=False))

    def test_quits_and_forgets_the_game_when_it_closes(self):
        track(alive=False)
        self.assertFalse(make_runner().keep_game_alive(set(), game_thread_running=False))
        self.assertIsNone(client_log.get_game_tracker("game-1"))

    def test_force_stop_forgets_the_game(self):
        track(alive=True)
        runner = make_runner()
        with (
            patch.object(wine, "winekill"),
            patch.object(wine.wine, "prefix_path", new_callable=PropertyMock, return_value="/pfx"),
            patch.object(wine.wine, "wine_arch", new_callable=PropertyMock, return_value="win64"),
            patch.object(wine.wine, "get_executable", return_value="wine"),
            patch.object(wine.wine, "get_env", return_value={}),
            patch.object(wine.Runner, "force_stop_game"),
        ):
            runner.force_stop_game(set())
        self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))
