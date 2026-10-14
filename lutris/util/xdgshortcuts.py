"""XDG shortcuts handling"""

import os
import shlex
import shutil
import stat
import sys

from gi.repository import GLib

from lutris.api import format_installer_url
from lutris.util import system
from lutris.util.linux import LINUX_SYSTEM
from lutris.util.log import logger

# KDE Plasma will not launch .desktop files on the Desktop without the execute bit.
_LAUNCHER_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH


def _escape_desktop_value(value: str) -> str:
    """Escape a Desktop Entry value (Name=, etc.)."""
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def _same_path(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normpath(left) == os.path.normpath(right)


def _running_lutris_script() -> str | None:
    """Absolute path to this process's Lutris entry-point script, if it exists."""
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return None
    if os.path.isabs(argv0) and os.path.isfile(argv0):
        return os.path.realpath(argv0)
    if os.path.sep in argv0 or argv0.startswith("."):
        candidate = os.path.abspath(argv0)
        if os.path.isfile(candidate):
            return os.path.realpath(candidate)
    found = shutil.which(argv0)
    if found:
        return os.path.realpath(found)
    return None


def _lutris_desktop_command() -> tuple[str, str | None]:
    """Return the Exec= command prefix and an optional TryExec= value."""
    if LINUX_SYSTEM.is_flatpak():
        return "flatpak run net.lutris.Lutris", None

    script = _running_lutris_script()
    which_lutris = shutil.which("lutris")
    if script and which_lutris and _same_path(script, which_lutris):
        return "lutris", "lutris"
    if script:
        return f"{shlex.quote(sys.executable)} {shlex.quote(script)}", script
    if which_lutris:
        return "lutris", "lutris"
    return "lutris", None


def get_lutris_executable() -> str:
    """Command used to relaunch this Lutris install from a .desktop Exec= key."""
    return _lutris_desktop_command()[0]


def get_lutris_try_exec() -> str | None:
    """Path or command name for TryExec=, or None to omit the key.

    A hardcoded TryExec=lutris hides the shortcut when Lutris is not on PATH
    (source checkouts, venv installs).
    """
    return _lutris_desktop_command()[1]


def get_xdg_entry(directory: str) -> str | None:
    """Return the path for specific user folders"""
    special_dir = {
        "DESKTOP": GLib.UserDirectory.DIRECTORY_DESKTOP,
        "MUSIC": GLib.UserDirectory.DIRECTORY_MUSIC,
        "PICTURES": GLib.UserDirectory.DIRECTORY_PICTURES,
        "VIDEOS": GLib.UserDirectory.DIRECTORY_VIDEOS,
        "DOCUMENTS": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
        "DOWNLOADS": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
        "TEMPLATES": GLib.UserDirectory.DIRECTORY_TEMPLATES,
    }
    directory = directory.upper()
    if directory not in special_dir:
        raise ValueError(
            directory + " not supported. Only those folders are supported: " + ", ".join(special_dir.keys())
        )
    return GLib.get_user_special_dir(special_dir[directory])


def get_xdg_basename(game_slug: str, game_id: str, base_dir: str | None = None) -> str:
    """Return the filename for .desktop shortcuts"""
    if base_dir:
        # When base dir is provided, lookup possible combinations
        # and return the first match
        for path in [
            "net.lutris.{}-{}.desktop".format(game_slug, game_id),
            "{}-{}.desktop".format(game_slug, game_id),
            "{}.desktop".format(game_slug),
        ]:
            if system.path_exists(os.path.join(base_dir, path)):
                return path

    return "net.lutris.{}-{}.desktop".format(game_slug, game_id)


def _write_launcher_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as launcher:
        launcher.write(content)
    os.chmod(path, _LAUNCHER_MODE)


def create_launcher(
    game_slug: str,
    game_id: str,
    game_name: str,
    launch_config_name: str | None = None,
    desktop: bool = False,
    menu: bool = False,
) -> None:
    """Create a .desktop file."""
    desktop_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    if desktop and not desktop_dir:
        logger.error("Cannot create a desktop shortcut: XDG desktop directory is not set")
        desktop = False
    if not desktop and not menu:
        return

    lutris_executable, try_exec = _lutris_desktop_command()

    url = format_installer_url({"action": "rungameid", "game_slug": game_id, "launch_config_name": launch_config_name})

    # Quote URL for the shell but *also* quote %, which indicates a desktop file
    # field code in the Exec key.
    command = f"{lutris_executable} {shlex.quote(url)}".replace("%", "%%")

    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={_escape_desktop_value(game_name)}",
        f"Icon=lutris_{game_slug}",
        f"Exec=env LUTRIS_SKIP_INIT=1 {command}",
        "Categories=Game",
    ]
    if try_exec:
        lines.append(f"TryExec={try_exec}")
    launcher_content = "\n".join(lines) + "\n"

    launcher_filename = get_xdg_basename(game_slug, game_id)

    if desktop:
        assert desktop_dir  # set to False above when the XDG desktop directory is missing
        launcher_path = os.path.join(desktop_dir, launcher_filename)
        logger.debug("Creating Desktop icon in %s", launcher_path)
        _write_launcher_file(launcher_path, launcher_content)
    if menu:
        user_dir = os.path.expanduser("~/.local/share") if LINUX_SYSTEM.is_flatpak() else GLib.get_user_data_dir()
        menu_path = os.path.join(user_dir, "applications")
        launcher_path = os.path.join(menu_path, launcher_filename)
        logger.debug("Creating menu launcher in %s", launcher_path)
        _write_launcher_file(launcher_path, launcher_content)


def get_launcher_path(game_slug: str, game_id: str) -> str:
    """Return the path of a XDG game launcher.
    When legacy is set, it will return the old path with only the slug,
    otherwise it will return the path with slug + id
    """
    desktop_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
    if not desktop_dir:
        return ""

    return os.path.join(desktop_dir, get_xdg_basename(game_slug, game_id, base_dir=desktop_dir))


def get_menu_launcher_path(game_slug: str, game_id: str) -> str:
    """Return the path to a XDG menu launcher, prioritizing legacy paths if
    they exist
    """
    menu_dir = os.path.join(GLib.get_user_data_dir(), "applications")
    return os.path.join(menu_dir, get_xdg_basename(game_slug, game_id, base_dir=menu_dir))


def desktop_launcher_exists(game_slug: str, game_id: str) -> bool:
    """Return True if there is an existing desktop icon for a game"""
    return system.path_exists(get_launcher_path(game_slug, game_id))


def menu_launcher_exists(game_slug: str, game_id: str) -> bool:
    """Return True if there is an existing application menu entry for a game"""
    return system.path_exists(get_menu_launcher_path(game_slug, game_id))


def remove_launcher(game_slug: str, game_id: str, desktop: bool = False, menu: bool = False) -> None:
    """Remove existing .desktop file."""
    if desktop:
        launcher_path = get_launcher_path(game_slug, game_id)
        if system.path_exists(launcher_path):
            os.remove(launcher_path)

    if menu:
        menu_path = get_menu_launcher_path(game_slug, game_id)
        if system.path_exists(menu_path):
            os.remove(menu_path)
