"""Tests for reading the game catalog cached by Battle.net."""

import json
import os
from tempfile import TemporaryDirectory
from unittest import TestCase

from lutris.util.battlenet.catalog import BattleNetCatalog

KEY_ART_HASH = "d36c5deb8c5b9965bbdd286eb1459377"

WOW_FRAGMENT = {
    "products": [
        {
            "id": "WoW",
            "base": {
                "uid": "wow",
                "name": "world_of_warcraft#WOW_NAME",
                "types": {
                    "retail": {"background": "world_of_warcraft#WOW_BACKGROUND"},
                    "wow_classic": {"uid": "wow_classic", "name": "world_of_warcraft#WOW_CLASSIC_NAME"},
                    "wowf": {
                        "uid": "wowf",
                        "name": "world_of_warcraft#WOW_FOREVER_NAME",
                        "key_art": "world_of_warcraft#WOW_FOREVER_KEY_ART",
                    },
                },
            },
        },
        {"id": "WoWF", "base": {"name": "world_of_warcraft#WOW_FOREVER_NAME"}},
    ],
    "strings": {
        "default": {
            "world_of_warcraft#WOW_NAME": "World of Warcraft",
            "world_of_warcraft#WOW_FOREVER_NAME": "WoW: Forever",
        },
        "deDE": {"world_of_warcraft#WOW_NAME": "Welt"},
    },
    "files": {
        "default": {
            "world_of_warcraft#WOW_FOREVER_KEY_ART": {"hash": KEY_ART_HASH, "name": "key_art_wow_forever.jpg"},
        },
    },
}

DIABLO_FRAGMENT = {
    "products": [
        {
            "id": "Fen",
            "base": {
                "name": "diablo_iv#FEN_NAME",
                "types": {"retail": {"uid": "fenris"}, "beta": {"uid": "fenris_beta"}},
            },
        }
    ],
    "strings": {"enUS": {"diablo_iv#FEN_NAME": "Diablo IV"}},
}


class BattleNetCatalogTest(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.prefix = directory.name
        self.cache_dir = os.path.join(self.prefix, "drive_c", "users", "steamuser", "AppData", "Local", "Battle.net")
        self.cache_dir = os.path.join(self.cache_dir, "Cache")
        self.write_cache_file("0a1b", json.dumps(WOW_FRAGMENT).encode())
        self.write_cache_file("2c3d", json.dumps(DIABLO_FRAGMENT).encode())
        self.write_cache_file("4e5f", b'{"not": "a catalog"}')
        self.write_cache_file("6a7b", b"{broken json")
        self.write_cache_file(KEY_ART_HASH, b"\xff\xd8\xff\xe0 jpeg data")

    def write_cache_file(self, name, data):
        folder = os.path.join(self.cache_dir, name[:2], name[2:4])
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), "wb") as cache_file:
            cache_file.write(data)

    def test_launch_codes_come_from_the_product_and_its_type(self):
        catalog = BattleNetCatalog(self.prefix)
        self.assertEqual(catalog.get_launch_code("wow"), "WoW")
        self.assertEqual(catalog.get_launch_code("wow_classic"), "WoW_wow_classic")
        self.assertEqual(catalog.get_launch_code("wowf"), "WoW_wowf")
        self.assertEqual(catalog.get_launch_code("fenris"), "Fen")
        self.assertEqual(catalog.get_launch_code("fenris_beta"), "Fen_beta")
        self.assertIsNone(catalog.get_launch_code("s1"))

    def test_names_use_the_default_or_english_strings(self):
        catalog = BattleNetCatalog(self.prefix)
        self.assertEqual(catalog.products["wow"].name, "World of Warcraft")
        self.assertEqual(catalog.products["wowf"].name, "WoW: Forever")
        self.assertEqual(catalog.products["fenris"].name, "Diablo IV")
        self.assertIsNone(catalog.products["wow_classic"].name)

    def test_key_art_is_found_in_the_cache(self):
        catalog = BattleNetCatalog(self.prefix)
        key_art_path = catalog.products["wowf"].key_art_path
        self.assertEqual(os.path.basename(key_art_path), KEY_ART_HASH)
        self.assertIsNone(catalog.products["wow"].key_art_path)

    def test_main_game_is_preferred_by_launch_code(self):
        catalog = BattleNetCatalog(self.prefix)
        self.assertEqual(catalog.get_product_by_launch_code("WoW").uid, "wow")
        self.assertIsNone(catalog.get_product_by_launch_code("S1"))

    def test_linked_user_folders_are_read_once(self):
        os.symlink("steamuser", os.path.join(self.prefix, "drive_c", "users", "someone"))
        self.assertEqual(BattleNetCatalog(self.prefix).get_launch_code("wowf"), "WoW_wowf")

    def test_missing_prefix_has_no_products(self):
        self.assertEqual(BattleNetCatalog(None).products, {})
        self.assertEqual(BattleNetCatalog(os.path.join(self.prefix, "missing")).products, {})
