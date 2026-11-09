"""Battle.net service"""

import json
import os
import shutil
from gettext import gettext as _

from gi.repository import Gio

from lutris import settings
from lutris.config import LutrisConfig, write_game_config
from lutris.database.games import add_game, get_game_by_field, get_games, update_existing
from lutris.database.services import ServiceGameCollection
from lutris.game import Game
from lutris.services.base import BaseService
from lutris.services.lutris import sync_media
from lutris.services.service_game import ServiceGame
from lutris.services.service_media import ServiceMedia
from lutris.util import system
from lutris.util.battlenet.catalog import BattleNetCatalog, CatalogProduct
from lutris.util.battlenet.definitions import ProductDbInfo
from lutris.util.battlenet.product_db import ProductDb
from lutris.util.log import logger
from lutris.util.strings import slugify

GAME_IDS = {
    "s1": ("s1", "StarCraft", "S1", "starcraft-remastered"),
    "s2": ("s2", "StarCraft II", "S2", "starcraft-ii"),
    "wow": ("wow", "World of Warcraft", "WoW", "world-of-warcraft"),
    "wow_classic": ("wow_classic", "World of Warcraft Classic", "WoW_wow_classic", "world-of-warcraft-classic"),
    "wow_classic_era": (
        "wow_classic_era",
        "World of Warcraft Classic Era",
        "WoW_wow_classic_era",
        "world-of-warcraft-classic",
    ),
    "pro": ("pro", "Overwatch 2", "Pro", "overwatch-2"),
    "w1": ("w1", "Warcraft: Orcs & Humans", "W1", "warcraft-orcs-humans"),
    "w1r": ("w1r", "Warcraft I: Remastered", "W1R", "warcraft-i-remastered"),
    "w2bn": ("w2bn", "Warcraft II: Battle.net Edition", "W2BN", "warcraft-ii-battle-net-edition"),
    "w2r": ("w2r", "Warcraft II: Remastered", "W2R", "warcraft-ii-remastered"),
    "w3": ("w3", "Warcraft III", "W3", "warcraft-iii-reforged"),
    "gryphon": ("gryphon", "Warcraft Rumble", "GRY", "warcraft-rumble"),
    "hsb": ("hsb", "Hearthstone", "WTCG", "hearthstone"),
    "hero": ("hero", "Heroes of the Storm", "Hero", "heroes-of-the-storm"),
    "d3cn": ("d3cn", "暗黑破壞神III", "D3CN", "diablo-iii"),
    "d3": ("d3", "Diablo III", "D3", "diablo-iii"),
    "fenris": ("fenris", "Diablo IV", "Fen", "diablo-iv"),
    "viper": ("viper", "Call of Duty: Black Ops 4", "VIPR", "call-of-duty-black-ops-4"),
    "odin": ("odin", "Call of Duty: Modern Warfare", "ODIN", "call-of-duty-modern-warfare"),
    "lazarus": (
        "lazarus",
        "Call of Duty: MW2 Campaign Remastered",
        "LAZR",
        "call-of-duty-modern-warfare-2-campaign-remastered",
    ),
    "zeus": ("zeus", "Call of Duty: Black Ops Cold War", "ZEUS", "call-of-duty-black-ops-cold-war"),
    "auks": ("auks", "Call of Duty: Modern Warfare II", "AUKS", "call-of-duty-modern-warfare-ii"),
    "codhq": ("codhq", "Call of Duty HQ", "CODHQ", "call-of-duty-hq"),
    "rtro": ("rtro", "Blizzard Arcade Collection", "RTRO", "blizzard-arcade-collection"),
    "wlby": ("wlby", "Crash Bandicoot 4: It's About Time", "WLBY", "crash-bandicoot-4-its-about-time"),
    "osi": ("osi", "Diablo® II: Resurrected", "OSI", "diablo-2-ressurected"),
    "fore": ("fore", "Call of Duty: Vanguard", "FORE", "call-of-duty-vanguard"),
    "d2": ("d2", "Diablo® II", "Diablo II", "diablo-ii"),
    "d2LOD": ("d2LOD", "Diablo® II: Lord of Destruction®", "Diablo II", "diablo-ii-lord-of-destruction"),
    "w3ROC": ("w3ROC", "Warcraft® III: Reign of Chaos", "Warcraft III", "warcraft-iii-reign-of-chaos"),
    "w3tft": ("w3tft", "Warcraft® III: The Frozen Throne®", "Warcraft III", "warcraft-iii-the-frozen-throne"),
    "sca": ("sca", "StarCraft® Anthology", "Starcraft", "starcraft"),
    "anbs": ("anbs", "Diablo Immortal", "ANBS", "diablo-immortal"),
}

PRODUCT_CODES = {game[0]: game[2] for game in GAME_IDS.values()}


def copy_key_art(key_art_path, dest_path):
    try:
        shutil.copyfile(key_art_path, dest_path)
    except OSError as ex:
        logger.warning("Can't copy Battle.net key art %s: %s", key_art_path, ex)
        return False
    return True


def get_key_art_path(slug):
    """Return the Battle.net key art found for a service game, if it is still in Battle.net's cache."""
    service_games = ServiceGameCollection.get_service_games(filters={"service": "battlenet", "slug": slug})
    details = json.loads(service_games[0]["details"]) if service_games and service_games[0].get("details") else {}
    key_art_path = details.get("key_art")
    return key_art_path if key_art_path and os.path.isfile(key_art_path) else None


class BattleNetCover(ServiceMedia):
    service = "battlenet"
    size = (176, 234)
    file_patterns = ["%s.jpg"]
    dest_path = os.path.join(settings.CACHE_DIR, "battlenet/coverart")
    api_field = "coverart"

    def download(self, slug, url):
        """Download the cover from lutris.net, or use Battle.net's own key art when it has none."""
        result = super().download(slug, url)
        cache_path = os.path.join(self.dest_path, self.get_filename(slug))
        if system.path_exists(cache_path, exclude_empty=True):
            return result
        key_art_path = get_key_art_path(slug)
        if key_art_path and copy_key_art(key_art_path, cache_path):
            return cache_path
        return result


class BattleNetGame(ServiceGame):
    """Game from Battle.net"""

    service = "battlenet"
    runner = "wine"
    installer_slug = "battlenet"

    @classmethod
    def create(cls, blizzard_game, key_art_path=None):
        """Create a service game from an entry of GAME_IDS"""
        service_game = cls()
        service_game.appid = blizzard_game[0]
        service_game.name = blizzard_game[1]
        service_game.slug = blizzard_game[3]
        service_game.details = json.dumps(
            {
                "id": blizzard_game[0],
                "name": blizzard_game[1],
                "product_code": blizzard_game[2],
                "slug": blizzard_game[3],
                "coverart": "https://lutris.net/games/cover/%s.jpg" % blizzard_game[3],
                "key_art": key_art_path,
            }
        )
        return service_game

    @classmethod
    def create_from_catalog(cls, product: CatalogProduct):
        """Create a service game for a game that GAME_IDS doesn't know, from Battle.net's catalog"""
        name = product.name or product.uid
        return cls.create((product.uid, name, product.launch_code, slugify(name)), product.key_art_path)


class BattleNetService(BaseService):
    """Service class for Battle.net"""

    id = "battlenet"
    name = _("Battle.net")
    icon = "battlenet"
    runner = "wine"
    medias = {"coverart": BattleNetCover}
    default_format = "coverart"
    client_installer = "battlenet"
    cookies_path = os.path.join(settings.CACHE_DIR, ".bnet.auth")
    cache_path = os.path.join(settings.CACHE_DIR, "bnet-library.json")
    redirect_uris = ["https://lutris.net"]

    @property
    def battlenet_config_path(self):
        return ""

    def _get_client_prefix(self):
        bnet_game = get_game_by_field(self.client_installer, "slug")
        if not bnet_game or not bnet_game.get("directory"):
            return None
        return bnet_game["directory"].split("drive_c")[0]

    def load(self):
        catalog = BattleNetCatalog(self._get_client_prefix())
        games = []
        for game in GAME_IDS.values():
            product = catalog.get_product_by_launch_code(game[2])
            games.append(BattleNetGame.create(game, product.key_art_path if product else None))
        for game in games:
            game.save()
        return games

    @staticmethod
    def _get_product_code(db_game):
        """Return the code Battle.net launches a service game with."""
        details = json.loads(db_game["details"]) if db_game.get("details") else {}
        return details.get("product_code") or PRODUCT_CODES.get(db_game["appid"], db_game["appid"])

    def _mark_games_uninstalled(self):
        """Mark all Battle.net games as uninstalled when the client is missing."""
        for game in get_games(filters={"service": self.id, "installed": "1"}):
            update_existing(id=game["id"], installed=0)

    def add_installed_games(self):
        """Scan an existing Battle.net install for games"""
        bnet_game = get_game_by_field(self.client_installer, "slug")
        if not bnet_game or not bnet_game.get("directory"):
            self._mark_games_uninstalled()
            return
        bnet_prefix = bnet_game["directory"].split("drive_c")[0]
        product_db_path = bnet_prefix + BlizzardProductDbParser.PRODUCT_DB_PATH
        if not os.path.exists(product_db_path):
            self._mark_games_uninstalled()
            return
        parser = BlizzardProductDbParser(bnet_prefix)
        catalog = BattleNetCatalog(bnet_prefix)
        installed_slugs = []
        for game in parser.games:
            slug = self.install_from_battlenet(bnet_game, game, catalog)
            if slug:
                installed_slugs.append(slug)
        sync_media(installed_slugs)
        self._add_missing_covers(installed_slugs)

    def _get_service_game(self, app_id, catalog):
        """Return the service game of an installed Battle.net game. Battle.net's catalog is used
        for games that GAME_IDS lists under another id, and to add the games it doesn't list."""
        service_game = ServiceGameCollection.get_game(self.id, app_id)
        if service_game:
            return service_game
        product = catalog.products.get(app_id)
        if not product:
            return None
        for game_id, game in GAME_IDS.items():
            if game[2] == product.launch_code:
                return ServiceGameCollection.get_game(self.id, game_id)
        BattleNetGame.create_from_catalog(product).save()
        return ServiceGameCollection.get_game(self.id, app_id)

    @staticmethod
    def _add_missing_covers(slugs):
        """Use Battle.net's key art for installed games that lutris.net has no cover for."""
        for slug in slugs:
            cover_path = os.path.join(settings.COVERART_PATH, slug + ".jpg")
            key_art_path = get_key_art_path(slug)
            if key_art_path and not system.path_exists(cover_path, exclude_empty=True):
                copy_key_art(key_art_path, cover_path)

    def install_from_battlenet(self, bnet_game, game, catalog=None):
        app_id = game.ngdp
        logger.debug("Installing Battle.net game %s", app_id)
        service_game = self._get_service_game(app_id, catalog or BattleNetCatalog(None))
        if not service_game:
            logger.error("Aborting install, %s is not present in the game library.", app_id)
            return
        lutris_game_id = service_game["slug"] + "-" + self.id
        existing_game = get_game_by_field(lutris_game_id, "installer_slug")
        if existing_game:
            return
        game_config = LutrisConfig(game_config_id=bnet_game["configpath"]).game_level
        product_code = (catalog and catalog.get_launch_code(app_id)) or self._get_product_code(service_game)
        game_config["game"]["args"] = '--exec="launch %s"' % product_code
        bnet_exe = game_config["game"].get("exe", "")
        if bnet_exe:
            if not os.path.isabs(bnet_exe):
                bnet_exe = os.path.join(game_config["game"].get("prefix", ""), bnet_exe)
            game_config["game"]["client_exe"] = bnet_exe
        configpath = write_game_config(lutris_game_id, game_config)
        slug = service_game["slug"]
        add_game(
            name=service_game["name"],
            runner=bnet_game["runner"],
            slug=slug,
            directory=bnet_game["directory"],
            installed=1,
            installer_slug=lutris_game_id,
            configpath=configpath,
            service=self.id,
            service_id=service_game["appid"],
            platform="Windows",
        )
        return slug

    def get_installed_slug(self, db_game):
        return db_game["slug"]

    def generate_installers(self, db_game):
        """Generate an installer that requires the Battle.net client.
        The dependency system will prompt the user to install it if missing."""
        return [
            {
                "name": db_game["name"],
                "version": self.name,
                "slug": db_game["slug"] + "-" + self.id,
                "game_slug": self.get_installed_slug(db_game),
                "runner": self.runner,
                "appid": db_game["appid"],
                "script": {
                    "requires": self.client_installer,
                    "game": {
                        "args": '--exec="launch %s"' % self._get_product_code(db_game),
                    },
                    "installer": [],
                },
            }
        ]

    def generate_installer(self, db_game, bnet_db_game):
        bnet_app = Game(bnet_db_game["id"])
        bnet_exe = bnet_app.config.game_config["exe"]
        if not os.path.isabs(bnet_exe):
            bnet_exe = os.path.join(bnet_app.config.game_config["prefix"], bnet_exe)
        return {
            "name": db_game["name"],
            "version": self.name,
            "slug": db_game["slug"] + "-" + self.id,
            "game_slug": self.get_installed_slug(db_game),
            "runner": self.get_installed_runner_name(db_game),
            "appid": db_game["appid"],
            "script": {
                "requires": self.client_installer,
                "game": {
                    "exe": bnet_exe,
                    "client_exe": bnet_exe,
                    "args": '--exec="launch %s"' % self._get_product_code(db_game),
                },
                "installer": [
                    {
                        "task": {
                            "name": "wineexec",
                            "executable": bnet_exe,
                            "args": '--exec="install %s"' % self._get_product_code(db_game),
                            "prefix": bnet_app.config.game_config["prefix"],
                            "description": (
                                "Battle.net will now open. Please launch "
                                "the installation of %s then close Battle.net "
                                "once the game has been downloaded." % db_game["name"]
                            ),
                        }
                    }
                ],
            },
        }

    def get_installed_runner_name(self, db_game):
        return self.runner

    def install(self, db_game, update=False):
        bnet_game = get_game_by_field(self.client_installer, "slug")
        if not bnet_game or not bnet_game.get("installed"):
            return super().install(db_game, update)
        application = Gio.Application.get_default()
        application.show_installer_window(
            [self.generate_installer(db_game, bnet_game)], service=self, appid=db_game["appid"]
        )


class BlizzardProductDbParser:
    # Adapted from DatabaseParser in https://github.com/bartok765/galaxy_blizzard_plugin
    NOT_GAMES = ("bna", "agent")
    PRODUCT_DB_PATH = "/drive_c/ProgramData/Battle.net/Agent/product.db"

    def __init__(self, prefix_path):
        self.data = self.load_product_db(prefix_path + self.PRODUCT_DB_PATH)
        self.products = {}
        self._region = ""
        self.parse()

    @property
    def region(self):
        return self._region

    @staticmethod
    def load_product_db(product_db_path):
        with open(product_db_path, "rb") as f:
            pdb = f.read()
        return pdb

    @property
    def games(self):
        if self.products:
            return [v for k, v in self.products.items() if k not in self.NOT_GAMES]
        return []

    def parse(self):
        self.products = {}
        database = ProductDb()
        database.decode(self.data)

        for product_install in database.product_installs:  # pylint: disable=no-member
            # process region
            if product_install.product_code in ["agent", "bna"] and not self.region:
                self._region = product_install.settings.play_region

            ngdp_code = product_install.product_code
            uninstall_tag = product_install.uid
            install_path = product_install.settings.install_path
            playable = product_install.cached_product_state.base_product_state.playable
            version = product_install.cached_product_state.base_product_state.current_version_str
            installed = product_install.cached_product_state.base_product_state.installed

            self.products[ngdp_code] = ProductDbInfo(
                uninstall_tag, ngdp_code, install_path, version, playable, installed
            )
