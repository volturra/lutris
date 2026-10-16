"""Resolve and classify local Windows executables opened with Lutris.

File managers pass leftover command-line arguments (paths or file:// URIs)
rather than lutris: URIs. This module decides whether that file is already a
library game, looks like an installer, or should be added as a local game.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from lutris.util.log import logger
from lutris.util.wine.prefix import find_prefix, is_prefix

if TYPE_CHECKING:
    from lutris.database.games import DbGameDict

KIND_INSTALLER = "installer"
KIND_GAME = "game"
KIND_UNKNOWN = "unknown"

WINDOWS_EXECUTABLE_EXTENSIONS = {".exe", ".msi"}
INSTALLER_SCRIPT_EXTENSIONS = {".yml", ".yaml", ".lutris"}

# setup.exe, setup_game.exe, game-setup.exe, Install.exe, game_installer.exe, game_update.exe.
# A bare update.exe is left out: games often ship one as a launcher updater.
_INSTALLER_NAME_RE = re.compile(
    r"""^(?:
            (?:setup|install(?:er)?|patch)
            (?:[-_.].*)?
        |
            .+[-_](?:setup|install(?:er)?|patch|update|upgrade)
        )\.(?:exe|msi)$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_UNINSTALL_NAME_RE = re.compile(
    r"^(?:unins\d*|uninstall|uninst)(?:[-_.].*)?\.(?:exe|msi)$",
    re.IGNORECASE,
)
_INSTALLER_NAME_PREFIXES = ("setup_", "patch_", "update_", "upgrade_")
# Names that say nothing about the game; better left blank for the user to fill in.
_GENERIC_INSTALLER_TITLES = {"setup", "install", "installer", "patch", "update", "upgrade"}

# Common Windows installer product signatures (Inno, NSIS, InstallShield, Wise, install4j, WiX Burn, etc.).
# Lower-case, since matching ignores case. Do not match "GOG.com": that string
# appears in installed GOG game binaries.
_INSTALLER_SIGNATURES = (
    b"inno setup",
    b"nullsoftinst",
    b"nullsoft install system",
    b"installshield",
    b"issetup.dll",
    b"setup.inx",
    b"wisemain",
    b"wise installation wizard",
    b"install4j",
    b"wixbundle",
    b"advanced installer",
)
# Installers also store these strings as UTF-16 resources.
_INSTALLER_SIGNATURE_PATTERNS = tuple(
    pattern
    for signature in _INSTALLER_SIGNATURES
    for pattern in (signature, signature.decode("ascii").encode("utf-16-le"))
)

# GOG offline volumes are setup_foo.exe + setup_foo-1.bin (numeric index only).
_GOG_BIN_VOLUME_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d+)\.bin$", re.IGNORECASE)

_PE_HEAD_BYTES = 512 * 1024
_PE_TAIL_BYTES = 256 * 1024


def is_windows_executable(path: str) -> bool:
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in WINDOWS_EXECUTABLE_EXTENSIONS


def is_lutris_installer_script(path: str) -> bool:
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in INSTALLER_SCRIPT_EXTENSIONS


def looks_like_lutris_invocation(arg: str) -> bool:
    """True if this token is the Lutris program itself, not a game file."""
    if not arg:
        return False
    expanded = os.path.abspath(os.path.expanduser(arg.split()[0]))
    base = os.path.basename(expanded)
    return base == "lutris" or expanded.endswith("/bin/lutris")


def looks_like_python_interpreter(arg: str) -> bool:
    if not arg or arg.startswith("-"):
        return False
    base = os.path.basename(arg.split()[0])
    return base.startswith("python")


def looks_like_local_file_arg(arg: str) -> bool:
    """True if a leftover CLI argument is a filesystem path or file:// URI.

    lutris: URIs must keep going through parse_installer_url.
    """
    if not arg or arg.startswith("lutris:"):
        return False
    if arg.startswith("file:"):
        return True
    if arg.startswith(("/", "./", "../", "~/")):
        return True
    lower = arg.lower()
    if any(lower.endswith(ext) for ext in WINDOWS_EXECUTABLE_EXTENSIONS | INSTALLER_SCRIPT_EXTENSIONS):
        return True
    return os.sep in arg


def resolve_local_file_arg(arg: str, cwd: str | None = None) -> str | None:
    """Turn a leftover argv token into an absolute path, or None if it is not local."""
    if not looks_like_local_file_arg(arg):
        return None
    path = arg.strip()
    if path.startswith("file:"):
        parsed = urlparse(path)
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
            logger.info("Ignoring non-local file URI %s", arg)
            return None
    path = os.path.expanduser(path)
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    return os.path.abspath(path)


def first_local_path_from_args(args: list[str], cwd: str | None = None) -> str | None:
    """Return the first leftover argument that is a local file, if any."""
    for arg in args:
        if looks_like_lutris_invocation(arg) or looks_like_python_interpreter(arg) or arg.startswith("-"):
            continue
        path = resolve_local_file_arg(arg, cwd)
        if path:
            return path
    return None


def paths_refer_to_same_file(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left = os.path.abspath(os.path.expanduser(left))
    right = os.path.abspath(os.path.expanduser(right))
    if left == right:
        return True
    if os.path.normcase(left) == os.path.normcase(right):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _is_inside_directory(path: str, directory: str) -> bool:
    if not directory:
        return False
    path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.abspath(os.path.expanduser(directory))
    if path == directory:
        return True
    prefix = directory if directory.endswith(os.sep) else directory + os.sep
    return path.startswith(prefix) or os.path.normcase(path).startswith(os.path.normcase(prefix))


def _normalize_path(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def _looks_like_leftover_installer(path: str) -> bool:
    """True if this path is an installer we should not launch as a library game.

    PE string hits are ignored on a prefix C: drive, where installed game
    binaries often contain them (see classify_windows_executable).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".msi":
        return True
    if has_installer_filename(path):
        return True
    return not is_inside_prefix_drive_c(path) and has_installer_pe_signature(path)


def _path_belongs_to_game_directory(path: str, directory: str) -> bool:
    """True if path is part of this library game's files, not a sibling download.

    When the game directory is a Wine prefix, only files on drive_c count.
    Otherwise leftover setups in ~/tmp would match a game whose directory is $HOME.
    """
    if not _is_inside_directory(path, directory):
        return False
    if is_prefix(directory):
        return _is_inside_directory(path, os.path.join(directory, "drive_c"))
    return True


def find_installed_game_for_path(path: str) -> "DbGameDict | None":
    """Return the installed library game that owns this file, if uniquely identifiable."""
    from lutris.database.games import get_game_by_field, get_games
    from lutris.util.path_cache import read_path_cache

    path = _normalize_path(path)
    for game_id, cached_path in read_path_cache().items():
        if paths_refer_to_same_file(path, cached_path):
            db_game = get_game_by_field(game_id, "id")
            if db_game and db_game.get("installed"):
                return db_game

    if _looks_like_leftover_installer(path):
        return None

    directory_matches = []
    for db_game in get_games(filters={"installed": 1}):
        directory = db_game.get("directory") or ""
        if _path_belongs_to_game_directory(path, directory):
            directory_matches.append(db_game)
    if len(directory_matches) == 1:
        return directory_matches[0]
    return None


def has_installer_filename(path: str) -> bool:
    filename = os.path.basename(path)
    if _UNINSTALL_NAME_RE.match(filename):
        return False
    if _INSTALLER_NAME_RE.match(filename):
        return True
    lower = filename.lower()
    return lower.startswith(_INSTALLER_NAME_PREFIXES)


def has_adjacent_bin_volumes(path: str) -> bool:
    """True if a GOG-style setup_foo-N.bin volume sits next to this EXE."""
    if not path or not os.path.isfile(path):
        return False
    directory = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    try:
        names = os.listdir(directory)
    except OSError:
        return False
    for name in names:
        match = _GOG_BIN_VOLUME_RE.match(name)
        if match and match.group("stem").lower() == stem:
            return True
    return False


def has_installer_pe_signature(path: str) -> bool:
    """Scan a small head/tail window of a PE for known installer product strings."""
    if not path:
        return False
    path = _normalize_path(path)
    if not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            data = handle.read(_PE_HEAD_BYTES)
            if size > _PE_HEAD_BYTES + _PE_TAIL_BYTES:
                handle.seek(size - _PE_TAIL_BYTES)
                data += handle.read(_PE_TAIL_BYTES)
            elif size > _PE_HEAD_BYTES:
                data += handle.read()
    except OSError as ex:
        logger.debug("Unable to read %s for installer signatures: %s", path, ex)
        return False
    data = data.lower()
    return any(pattern in data for pattern in _INSTALLER_SIGNATURE_PATTERNS)


def is_inside_prefix_drive_c(path: str) -> bool:
    """True if the file lives on a Wine prefix C: drive.

    find_prefix() walks ancestors and also accepts a sibling prefix/ or pfx/
    directory. That is useful when prefilling a prefix, but it is not the same
    as "this is an installed Windows game". A download in ~/tmp when $HOME is a
    prefix, or setup.exe next to a Proton pfx, must still be classified as an
    installer.
    """
    prefix = find_prefix(path)
    if not prefix:
        return False
    return _is_inside_directory(path, os.path.join(prefix, "drive_c"))


def suggested_game_name(path: str) -> str:
    if not path:
        return ""
    path = _normalize_path(path)
    filename = os.path.basename(path)
    if has_installer_filename(path):
        from lutris.util.gog_offline import title_from_filename

        title = title_from_filename(filename)
        return "" if title.lower() in _GENERIC_INSTALLER_TITLES else title
    name = os.path.splitext(filename)[0] if os.path.isfile(path) else filename
    return re.sub(r"[\s_-]+", " ", name).strip() or filename


def classify_windows_executable(path: str) -> str:
    """Return KIND_INSTALLER, KIND_GAME, or KIND_UNKNOWN."""
    if not path:
        return KIND_UNKNOWN
    path = _normalize_path(path)
    if os.path.isdir(path):
        return KIND_INSTALLER
    ext = os.path.splitext(path)[1].lower()
    if ext == ".msi":
        return KIND_INSTALLER
    if ext != ".exe":
        return KIND_UNKNOWN

    in_drive_c = is_inside_prefix_drive_c(path)
    installer_name = has_installer_filename(path)
    has_bins = has_adjacent_bin_volumes(path)

    # GOG stacked installers: setup_foo.exe + setup_foo-1.bin, even inside a prefix.
    if has_bins and installer_name:
        return KIND_INSTALLER
    if in_drive_c:
        if installer_name:
            # Leftover setup.exe inside an already-created prefix is ambiguous.
            return KIND_UNKNOWN
        # Ignore PE string hits in ordinary game binaries (false positives).
        return KIND_GAME
    if installer_name or has_installer_pe_signature(path):
        return KIND_INSTALLER
    return KIND_UNKNOWN
