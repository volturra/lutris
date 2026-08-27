"""Group local GOG offline installers (EXE + BIN volumes) for one-shot install.

This does not talk to GOG.com. Titles and product IDs come from the installer
files themselves (innoextract) or from the filename. Lutris.net can later map
a product ID to a slug for banners.
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass, field
from gettext import gettext as _
from gettext import ngettext
from typing import Callable, Iterable

from lutris.installer import AUTO_ELF_EXE, AUTO_WIN32_EXE
from lutris.util.log import logger
from lutris.util.strings import human_size, slugify

# GOG Inno Setup volumes: setup_foo.exe + setup_foo-1.bin + setup_foo-2.bin
_BIN_VOLUME_RE = re.compile(r"^(?P<stem>.+)-(?P<index>\d+)\.bin$", re.IGNORECASE)
_SETUP_EXE_RE = re.compile(r"^(setup|patch)_.+\.exe$", re.IGNORECASE)
# GOG names are setup_<title>_<version>_(<build>).exe — strip version then build.
_VERSION_SUFFIX_RE = re.compile(r"(?:_\d+(?:\.\d+)*)?(?:_\(\d+\))?$", re.IGNORECASE)
_GOG_ID_RE = re.compile(r"GOG(?:\.com)? game ID[:\s]+(\d+)", re.IGNORECASE)
_INSPECT_TITLE_RE = re.compile(r'Inspecting\s+"([^"]+)"', re.IGNORECASE)

ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".txz"}

InspectFn = Callable[[str], tuple[str | None, str | None]]


@dataclass
class GogOfflinePackage:
    """One installable GOG offline installer (an EXE or Linux .sh, plus volumes)."""

    exe_path: str
    bin_paths: list[str] = field(default_factory=list)
    title: str = ""
    gogid: str | None = None
    kind: str = "windows"  # "windows" or "linux"
    role: str = "setup"  # "setup" or "patch"
    size: int = 0

    @property
    def filename(self) -> str:
        return os.path.basename(self.exe_path)

    @property
    def size_label(self) -> str:
        return human_size(self.size) if self.size else ""

    @property
    def subtitle(self) -> str:
        parts = []
        if self.size_label:
            parts.append(self.size_label)
        if self.bin_paths:
            n = len(self.bin_paths)
            parts.append(ngettext("%d bin volume", "%d bin volumes", n) % n)
        if self.role == "patch":
            parts.append(_("Patch"))
        elif self.kind == "linux":
            parts.append(_("Linux"))
        parts.append(self.filename)
        return " · ".join(parts)


def is_archive_path(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    ext = os.path.splitext(path)[1].lower()
    if ext in ARCHIVE_EXTENSIONS:
        return True
    # .tar.gz etc.
    lower = path.lower()
    return lower.endswith(".tar.gz") or lower.endswith(".tar.bz2") or lower.endswith(".tar.xz")


def is_generic_windows_setup(path: str) -> bool:
    """True if path is a Windows .exe usable as a fallback wineexec installer."""
    return bool(path) and os.path.isfile(path) and path.lower().endswith(".exe")


def bin_volume_stem(filename: str) -> str | None:
    """Return the installer stem if filename is a GOG -N.bin volume, else None."""
    match = _BIN_VOLUME_RE.match(filename)
    if not match:
        return None
    return match.group("stem")


def installer_stem(filename: str) -> str:
    """Stem used to match an EXE/SH to its .bin volumes."""
    root, ext = os.path.splitext(filename)
    if ext.lower() in {".exe", ".sh"}:
        return root
    return filename


def filename_name_tokens(filename: str) -> tuple[str, ...]:
    """Tokenize a GOG setup filename with version/build suffix removed."""
    name = os.path.splitext(os.path.basename(filename))[0].lower()
    if name.startswith("setup_"):
        name = name[len("setup_") :]
    elif name.startswith("patch_"):
        name = name[len("patch_") :]
    name = _VERSION_SUFFIX_RE.sub("", name)
    return tuple(token for token in name.split("_") if token)


def title_from_filename(filename: str) -> str:
    tokens = filename_name_tokens(filename)
    if not tokens:
        return os.path.splitext(os.path.basename(filename))[0]
    return " ".join(tokens).title()


def parse_innoextract_info(output: str) -> tuple[str | None, str | None]:
    """Parse `innoextract -i` text into (title, gogid)."""
    if not output:
        return None, None
    title = None
    gogid = None
    title_match = _INSPECT_TITLE_RE.search(output)
    if title_match:
        title = title_match.group(1).strip() or None
    id_match = _GOG_ID_RE.search(output)
    if id_match:
        gogid = id_match.group(1)
    if not gogid:
        stripped = output.strip()
        if stripped.isdigit():
            gogid = stripped
    return title, gogid


def is_linux_gog_installer(path: str) -> bool:
    """True if path is a GOG Linux mojosetup/.sh that contains data/noarch."""
    if not path.lower().endswith(".sh"):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.startswith("data/noarch") for name in archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def group_gog_files(file_paths: Iterable[str]) -> list[GogOfflinePackage]:
    """Group EXE/SH files with sibling -N.bin volumes. Does not inspect contents."""
    paths = [os.path.abspath(p) for p in file_paths if p]
    by_dir: dict[str, list[str]] = {}
    for path in paths:
        by_dir.setdefault(os.path.dirname(path), []).append(path)

    packages: list[GogOfflinePackage] = []
    for directory, dir_paths in by_dir.items():
        bins_by_stem: dict[str, list[str]] = {}
        installers: list[str] = []
        for path in sorted(dir_paths):
            name = os.path.basename(path)
            stem = bin_volume_stem(name)
            if stem:
                bins_by_stem.setdefault(stem, []).append(path)
                continue
            lower = name.lower()
            if lower.endswith(".exe") or lower.endswith(".sh"):
                installers.append(path)

        used_stems: set[str] = set()
        for exe_path in installers:
            name = os.path.basename(exe_path)
            stem = installer_stem(name)
            bins = sorted(bins_by_stem.get(stem, []))
            used_stems.add(stem)
            role = "patch" if name.lower().startswith("patch_") else "setup"
            kind = "linux" if name.lower().endswith(".sh") else "windows"
            size = _file_size(exe_path) + sum(_file_size(b) for b in bins)
            packages.append(
                GogOfflinePackage(
                    exe_path=exe_path,
                    bin_paths=bins,
                    title=title_from_filename(name),
                    kind=kind,
                    role=role,
                    size=size,
                )
            )

        for stem, bins in bins_by_stem.items():
            if stem not in used_stems:
                logger.warning("GOG bin volumes have no matching installer in %s: %s", directory, stem)

    return packages


def _common_token_prefix(token_lists: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not token_lists:
        return ()
    prefix: list[str] = []
    for parts in zip(*token_lists):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return tuple(prefix)


def suggest_package_order(
    packages: list[GogOfflinePackage], preferred_path: str | None = None
) -> list[GogOfflinePackage]:
    """Default install order. Never uses package size.

    Preferred EXE first if it is in the list. Otherwise, if exactly one
    non-patch package's filename tokens equal the common prefix of the set,
    that package is first. Patches are last. Remaining order is stable.
    """
    if not packages:
        return []

    preferred_abs = os.path.abspath(preferred_path) if preferred_path else None
    patches = [p for p in packages if p.role == "patch"]
    others = [p for p in packages if p.role != "patch"]

    def take_preferred(items: list[GogOfflinePackage]) -> GogOfflinePackage | None:
        if not preferred_abs:
            return None
        for item in items:
            if os.path.abspath(item.exe_path) == preferred_abs:
                return item
        return None

    ordered: list[GogOfflinePackage] = []
    preferred = take_preferred(others) or take_preferred(patches)
    remaining_others = [p for p in others if p is not preferred]
    remaining_patches = [p for p in patches if p is not preferred]

    if preferred and preferred.role != "patch":
        ordered.append(preferred)
    elif remaining_others:
        token_lists = [filename_name_tokens(p.filename) for p in remaining_others]
        prefix = _common_token_prefix(token_lists)
        base_candidates = [p for p, tokens in zip(remaining_others, token_lists) if tokens == prefix and prefix]
        if len(base_candidates) == 1:
            base = base_candidates[0]
            ordered.append(base)
            remaining_others = [p for p in remaining_others if p is not base]

    ordered.extend(remaining_others)
    if preferred and preferred.role == "patch":
        ordered.append(preferred)
    ordered.extend(remaining_patches)
    return ordered


def inspect_innoextract(path: str) -> tuple[str | None, str | None]:
    """Run innoextract -i and parse title / GOG product id. Fail-open."""
    import subprocess

    from lutris.exceptions import MissingExecutableError
    from lutris.util.extract import get_innoextract_path

    try:
        innoextract_path = get_innoextract_path()
    except MissingExecutableError:
        logger.debug("innoextract not available; skipping inspect of %s", path)
        return None, None

    try:
        completed = subprocess.run(
            [innoextract_path, "-i", path],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as ex:
        logger.debug("innoextract inspect failed for %s: %s", path, ex)
        return None, None

    text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return parse_innoextract_info(text)


def _enrich_package(package: GogOfflinePackage, inspect_fn: InspectFn) -> None:
    if package.kind != "windows":
        return
    title, gogid = inspect_fn(package.exe_path)
    if title:
        package.title = title
    if gogid:
        package.gogid = gogid


def scan_gog_offline_directory(
    directory: str,
    preferred_path: str | None = None,
    inspect_fn: InspectFn | None = None,
) -> list[GogOfflinePackage]:
    """Scan a folder for GOG offline installers and return ordered packages."""
    if not directory or not os.path.isdir(directory):
        return []

    inspect_fn = inspect_fn or inspect_innoextract
    candidates: list[str] = []
    preferred_abs = os.path.abspath(preferred_path) if preferred_path else None

    try:
        names = os.listdir(directory)
    except OSError as ex:
        logger.warning("Unable to list GOG offline folder %s: %s", directory, ex)
        return []

    for name in names:
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        lower = name.lower()
        if _SETUP_EXE_RE.match(name) or bin_volume_stem(name):
            candidates.append(path)
        elif lower.endswith(".sh") and is_linux_gog_installer(path):
            candidates.append(path)
        elif preferred_abs and os.path.abspath(path) == preferred_abs:
            candidates.append(path)

    if preferred_abs and os.path.isfile(preferred_abs) and preferred_abs not in candidates:
        candidates.append(preferred_abs)

    packages = group_gog_files(candidates)
    # Drop non-GOG preferred EXEs that sneaked in without setup_/patch_ naming
    # unless innoextract reports a GOG id or the user picked that file as a
    # lone installer (handled by the caller when the list is empty).
    kept: list[GogOfflinePackage] = []
    for package in packages:
        name = package.filename.lower()
        if package.kind == "linux":
            kept.append(package)
            continue
        if name.startswith("setup_") or name.startswith("patch_"):
            _enrich_package(package, inspect_fn)
            kept.append(package)
            continue
        if preferred_abs and os.path.abspath(package.exe_path) == preferred_abs:
            _enrich_package(package, inspect_fn)
            if package.gogid or _SETUP_EXE_RE.match(package.filename):
                kept.append(package)
    return suggest_package_order(kept, preferred_path=preferred_path)


def resolve_gog_offline_packages(path: str, inspect_fn: InspectFn | None = None) -> list[GogOfflinePackage]:
    """Scan a file, folder, or archive path for GOG offline packages."""
    path = os.path.expanduser(path)
    if not path or not os.path.exists(path):
        return []
    if os.path.isdir(path):
        return scan_gog_offline_directory(path, inspect_fn=inspect_fn)
    if is_archive_path(path):
        extracted = _extract_archive_to_cache(path)
        if extracted:
            return scan_gog_offline_directory(extracted, inspect_fn=inspect_fn)
        return []
    return scan_gog_offline_directory(os.path.dirname(path), preferred_path=path, inspect_fn=inspect_fn)


_EXTRACT_SENTINEL = ".lutris-complete"


def _gog_offline_cache_root() -> str:
    from lutris import settings

    return os.path.join(settings.CACHE_DIR, "gog-offline")


def _archive_cache_dir(archive_path: str) -> str:
    st = os.stat(archive_path)
    key = "%s:%s:%s" % (os.path.abspath(archive_path), st.st_mtime_ns, st.st_size)
    digest = hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(_gog_offline_cache_root(), digest)


def _cleanup_gog_offline_cache(keep: str | None = None) -> None:
    """Keep at most one extracted archive. Drops sibling extract dirs."""
    from lutris.util import system

    root = _gog_offline_cache_root()
    if not os.path.isdir(root):
        return
    keep_abs = os.path.abspath(keep) if keep else None
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if keep_abs and os.path.abspath(path) == keep_abs:
            continue
        if os.path.isdir(path):
            system.delete_folder(path)


def _extract_archive_to_cache(archive_path: str) -> str | None:
    from lutris.util import system
    from lutris.util.extract import extract_archive

    try:
        dest = _archive_cache_dir(archive_path)
    except OSError as ex:
        logger.error("Unable to stat GOG offline archive %s: %s", archive_path, ex)
        return None

    sentinel = os.path.join(dest, _EXTRACT_SENTINEL)
    if os.path.isfile(sentinel):
        return dest

    if os.path.isdir(dest):
        system.delete_folder(dest)

    os.makedirs(dest, exist_ok=True)
    try:
        extract_archive(archive_path, dest, merge_single=True)
        with open(sentinel, "wb"):
            pass
    except Exception as ex:
        logger.error("Failed to extract GOG offline archive %s: %s", archive_path, ex)
        system.delete_folder(dest)
        return None
    _cleanup_gog_offline_cache(keep=dest)
    return dest


def lookup_lutris_slug_for_gogid(gogid: str) -> str | None:
    """Map a GOG product id to a lutris.net slug. Fail-open, no GOG.com login."""
    if not gogid:
        return None
    try:
        from lutris.api import get_api_games

        games = get_api_games([str(gogid)], service="gog")
    except Exception as ex:
        logger.debug("lutris.net GOG slug lookup failed for %s: %s", gogid, ex)
        return None
    if games:
        return games[0].get("slug")
    return None


def _file_entry(file_id: str, package: GogOfflinePackage) -> dict:
    return {
        file_id: {
            "url": "N/A:" + _("Select the installer from GOG"),
            "filename": package.filename,
            "local_path": package.exe_path,
        }
    }


def build_offline_installer(
    packages: list[GogOfflinePackage],
    name: str,
    game_slug: str,
    wine_arch: str = "win64",
    locale: str | None = None,
    win_ver: str | None = None,
) -> dict:
    """Build a Lutris installer script for the selected packages, in list order.

    The first package creates the game (autosetup / linux extract). Further
    packages run in the same prefix and do not create extra library rows.
    """
    if not packages:
        raise ValueError("No GOG offline packages selected")

    kinds = {package.kind for package in packages}
    if len(kinds) > 1:
        raise ValueError(_("Linux and Windows GOG installers cannot be combined"))

    game_slug = slugify(game_slug or name)
    first = packages[0]
    gogid = first.gogid
    files = []
    installer_steps: list[dict] = []
    is_linux = first.kind == "linux"

    if win_ver and win_ver != "win10" and not is_linux:
        installer_steps.append({"task": {"name": "winetricks", "app": win_ver, "arch": wine_arch}})

    for index, package in enumerate(packages):
        file_id = f"gogsetup{index}"
        files.append(_file_entry(file_id, package))
        if is_linux:
            cache_dir = f"$CACHE/{file_id}"
            installer_steps.append({"extract": {"file": file_id, "format": "zip", "dst": cache_dir}})
            installer_steps.append({"merge": {"src": f"{cache_dir}/data/noarch", "dst": "$GAMEDIR"}})
        elif index == 0:
            installer_steps.append({"autosetup_gog_game": file_id})
        else:
            installer_steps.append(
                {
                    "task": {
                        "name": "wineexec",
                        "executable": file_id,
                        "prefix": "$GAMEDIR",
                        "args": "/SP- /NOCANCEL",
                    }
                }
            )

    if is_linux:
        runner = "linux"
        game_config = {"exe": AUTO_ELF_EXE}
    else:
        runner = "wine"
        game_config = {"exe": AUTO_WIN32_EXE, "prefix": "$GAMEDIR"}

    script: dict = {
        "game": game_config,
        "files": files,
        "installer": installer_steps,
    }
    if locale:
        script["system"] = {"env": {"LC_ALL": locale}}

    installer = {
        "name": name,
        "version": _("Offline installer"),
        "slug": game_slug + "-setup",
        "game_slug": game_slug,
        "runner": runner,
        "script": script,
    }
    if gogid:
        installer["gogid"] = gogid
        script["game"]["gogid"] = gogid
    return installer
