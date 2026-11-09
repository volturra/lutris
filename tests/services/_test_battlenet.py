"""Tests for finding the Battle.net client among installed Wine games."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris import settings
from lutris.config import write_game_config
from lutris.database import games, schema
from lutris.services.battlenet import BattleNetService, BlizzardProductDbParser

BATTLENET_EXE = "drive_c/Program Files (x86)/Battle.net/Battle.net.exe"


class TestFindBattleNetClient(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        for name, value in {"DB_PATH": str(self.directory / "pga.db"), "CONFIG_DIR": str(self.directory)}.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        schema.syncdb()
        (self.directory / "games").mkdir()
        self.prefix = self.directory / "prefix"
        product_db = Path(str(self.prefix) + BlizzardProductDbParser.PRODUCT_DB_PATH)
        product_db.parent.mkdir(parents=True)
        product_db.write_bytes(b"")

    def add_wine_game(self, slug, exe, prefix=None):
        game_config = {"exe": str(self.prefix / exe), "prefix": str(prefix or self.prefix)}
        configpath = write_game_config(slug, {"game": game_config, "wine": {}})
        return games.add_game(
            name=slug, slug=slug, runner="wine", installed=1, directory=str(self.prefix), configpath=configpath
        )

    def test_client_installed_by_another_game_is_found(self):
        game_id = self.add_wine_game("world-of-warcraft", BATTLENET_EXE)
        service = BattleNetService()
        self.assertEqual(service.get_client_game()["id"], game_id)
        self.assertTrue(service.is_launchable())
        self.assertEqual(service.get_launcher().id, game_id)

    def test_game_that_does_not_run_battlenet_is_skipped(self):
        self.add_wine_game("some-game", "drive_c/Games/game.exe")
        service = BattleNetService()
        self.assertIsNone(service.get_client_game())
        self.assertFalse(service.is_launchable())
        self.assertIsNone(service.get_launcher())

    def test_prefix_without_battlenet_agent_is_skipped(self):
        other_prefix = self.directory / "other"
        other_prefix.mkdir()
        self.add_wine_game("world-of-warcraft", BATTLENET_EXE, prefix=other_prefix)
        self.assertIsNone(BattleNetService().get_client_game())

    def test_prefix_comes_from_config_or_game_folder(self):
        db_game = {"directory": "/games/bnet/drive_c/Program Files (x86)/Battle.net"}
        self.assertEqual(BattleNetService._get_prefix(db_game, {"prefix": "/games/pfx/"}), "/games/pfx")
        self.assertEqual(BattleNetService._get_prefix(db_game, {}), "/games/bnet")
