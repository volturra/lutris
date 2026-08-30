import os
from gettext import gettext as _
from typing import TYPE_CHECKING

from lutris.config import LutrisConfig
from lutris.gui.config.game_common import GameDialogCommon

if TYPE_CHECKING:
    from gi.repository import Gtk

    from lutris.game import Game


class AddGameDialog(GameDialogCommon):
    """Add game dialog class."""

    def __init__(
        self,
        parent: "Gtk.Widget | None",
        game: "Game | None" = None,
        runner: str | None = None,
        exe: str | None = None,
        prefix: str | None = None,
        name: str | None = None,
    ):
        super().__init__(_("Add a new game"), config_level="game", parent=parent)
        self.game = game
        self.saved = False
        self.runner_name = (game.runner_name if game else None) or runner

        self.lutris_config = LutrisConfig(
            runner_slug=self.runner_name,
            level="game",
        )
        if exe:
            self.lutris_config.raw_game_config["exe"] = exe
            self.lutris_config.game_config["exe"] = exe
            working_dir = os.path.dirname(exe)
            if working_dir:
                self.lutris_config.raw_game_config["working_dir"] = working_dir
                self.lutris_config.game_config["working_dir"] = working_dir
        if prefix:
            self.lutris_config.raw_game_config["prefix"] = prefix
            self.lutris_config.game_config["prefix"] = prefix
        self.build_notebook()
        self.build_tabs()
        if name and self.info_box and self.info_box.name_entry:
            self.info_box.name_entry.set_text(name)
        if self.info_box:
            self.info_box.grab_focus()
        self.show_all()
