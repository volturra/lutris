"""Tests for the Battle.net service using the catalog that Battle.net caches."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris import settings
from lutris.database import schema
from lutris.database.services import ServiceGameCollection
from lutris.services.battlenet import BattleNetCover, BattleNetGame, BattleNetService
from lutris.util.battlenet.catalog import BattleNetCatalog, CatalogProduct


def make_catalog(*products):
    catalog = BattleNetCatalog(None)
    catalog.products = {product.uid: product for product in products}
    return catalog


class TestBattleNetCatalogService(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        for name, value in {"DB_PATH": str(self.directory / "pga.db"), "CONFIG_DIR": str(self.directory)}.items():
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        schema.syncdb()
        self.service = BattleNetService()
        with patch.object(BattleNetService, "_get_client_prefix", return_value=None):
            self.service.load()

    def test_game_missing_from_the_table_is_added_from_the_catalog(self):
        key_art = self.directory / "key_art"
        key_art.write_bytes(b"jpeg")
        catalog = make_catalog(CatalogProduct("wowf", "WoW_wowf", "WoW: Forever - Stress Test", str(key_art)))
        service_game = self.service._get_service_game("wowf", catalog)
        self.assertEqual(service_game["name"], "WoW: Forever - Stress Test")
        self.assertEqual(service_game["slug"], "wow-forever-stress-test")
        self.assertEqual(BattleNetService._get_product_code(service_game), "WoW_wowf")
        self.assertEqual(json.loads(service_game["details"])["key_art"], str(key_art))

    def test_game_listed_under_another_id_uses_the_table_entry(self):
        catalog = make_catalog(CatalogProduct("hs_beta", "WTCG", "Hearthstone"))
        service_game = self.service._get_service_game("hs_beta", catalog)
        self.assertEqual(service_game["appid"], "hsb")
        self.assertIsNone(ServiceGameCollection.get_game("battlenet", "hs_beta"))

    def test_unknown_game_is_not_added(self):
        self.assertIsNone(self.service._get_service_game("nothing", make_catalog()))

    def test_cover_falls_back_to_key_art(self):
        key_art = self.directory / "key_art"
        key_art.write_bytes(b"jpeg")
        BattleNetGame.create(("w1r", "Warcraft I", "W1R", "warcraft-i-remastered"), str(key_art)).save()
        with patch.object(BattleNetCover, "dest_path", str(self.directory)):
            cover_path = BattleNetCover().download("warcraft-i-remastered", None)
        self.assertEqual(Path(cover_path).read_bytes(), b"jpeg")
