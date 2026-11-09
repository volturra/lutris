"""Read the game catalog that the Battle.net client caches in its Wine prefix.

Battle.net downloads Blizzard's catalog as JSON fragments and keeps them in
``AppData/Local/Battle.net/Cache``, under file names made from their hashes. Each
fragment can hold ``products``, ``strings`` (display names per locale) and
``files`` (images, stored in the same cache under their hash). A product looks like::

    {"id": "WoW", "base": {"uid": "wow", "name": "...#WOW_NAME", "key_art": "...",
                           "types": {"wowf": {"uid": "wowf", "name": "...#WOW_FOREVER_NAME"}}}}

The product id is what Battle.net's ``--exec="launch <code>"`` expects. An install uid
(the ``ngdp`` code in product.db) maps to the product id for the main game or the
'retail' type, and to '<product id>_<type>' for other types such as 'wow_classic'.
"""

import glob
import json
import os
from dataclasses import dataclass

from lutris.util.log import logger

# Locales to read names and images from, in order of preference
LOCALES = ("default", "enUS")
RETAIL_TYPE = "retail"


@dataclass
class CatalogProduct:
    """What the catalog says about one installable game."""

    uid: str
    launch_code: str
    name: str | None = None
    key_art_path: str | None = None


def get_cache_dirs(prefix_path: str | None) -> list[str]:
    """Return the Battle.net cache folders of a Wine prefix, without the duplicates
    made by Proton linking 'steamuser' to the real user folder."""
    if not prefix_path:
        return []
    pattern = os.path.join(prefix_path, "drive_c", "users", "*", "AppData", "Local", "Battle.net", "Cache")
    return sorted({os.path.realpath(path) for path in glob.glob(pattern)})


def _get_launch_code(product_id: str, product_type: str | None) -> str:
    if not product_type or product_type == RETAIL_TYPE:
        return product_id
    return "%s_%s" % (product_id, product_type)


class BattleNetCatalog:
    """The products Battle.net knows about, by install uid."""

    def __init__(self, prefix_path: str | None) -> None:
        self.products: dict[str, CatalogProduct] = {}
        self._strings: dict[str, str] = {}
        self._files: dict[str, str] = {}
        self._cache_dir: str | None = None
        product_lists = []
        for cache_dir in get_cache_dirs(prefix_path):
            self._cache_dir = cache_dir
            for fragment in self._read_fragments(cache_dir):
                self._add_resources(fragment)
                product_lists.append(fragment["products"])
        for products in product_lists:
            for product in products:
                self._add_product(product)

    @staticmethod
    def _read_fragments(cache_dir: str):
        """Yield the catalog fragments among the cached files; most of the cache is images."""
        for path in glob.glob(os.path.join(cache_dir, "*", "*", "*")):
            try:
                with open(path, "rb") as cache_file:
                    if cache_file.read(1) != b"{":
                        continue
                    cache_file.seek(0)
                    fragment = json.loads(cache_file.read().decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as ex:
                logger.debug("Skipping Battle.net cache file %s: %s", path, ex)
                continue
            if isinstance(fragment, dict) and isinstance(fragment.get("products"), list):
                yield fragment

    def _add_resources(self, fragment: dict) -> None:
        """Collect a fragment's display names and image hashes, preferring the earlier locales."""
        for locale in reversed(LOCALES):
            strings = fragment.get("strings", {}).get(locale)
            if isinstance(strings, dict):
                self._strings.update({key: value for key, value in strings.items() if isinstance(value, str)})
            files = fragment.get("files", {}).get(locale)
            if isinstance(files, dict):
                self._files.update(
                    {key: value["hash"] for key, value in files.items() if isinstance(value, dict) and "hash" in value}
                )

    def _get_key_art_path(self, key: str | None) -> str | None:
        file_hash = self._files.get(key) if key else None
        if not file_hash or not self._cache_dir:
            return None
        path = os.path.join(self._cache_dir, file_hash[:2], file_hash[2:4], file_hash)
        return path if os.path.isfile(path) else None

    def _add_product(self, product: dict) -> None:
        product_id = product.get("id")
        base = product.get("base")
        if not isinstance(product_id, str) or not isinstance(base, dict):
            return
        entries = [(None, base)]
        types = base.get("types")
        if isinstance(types, dict):
            entries += [(product_type, entry) for product_type, entry in types.items() if isinstance(entry, dict)]
        for product_type, entry in entries:
            uid = entry.get("uid")
            if not isinstance(uid, str) or uid in self.products:
                continue
            name_key = entry.get("name") or base.get("name")
            self.products[uid] = CatalogProduct(
                uid=uid,
                launch_code=_get_launch_code(product_id, product_type),
                name=self._strings.get(name_key) if name_key else None,
                key_art_path=self._get_key_art_path(entry.get("key_art") or base.get("key_art")),
            )

    def get_product_by_launch_code(self, launch_code: str) -> CatalogProduct | None:
        """Return the product Battle.net launches with a code, preferring its main game."""
        products = [product for product in self.products.values() if product.launch_code == launch_code]
        return products[0] if products else None

    def get_launch_code(self, uid: str) -> str | None:
        """Return the code Battle.net launches an installed game with, if the catalog has it."""
        product = self.products.get(uid)
        return product.launch_code if product else None
