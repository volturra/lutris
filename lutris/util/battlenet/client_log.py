"""Follow the Battle.net client's own log to learn when it is ready and which games it runs.

Battle.net ignores ``--exec="launch <code>"`` while it is still starting up, and a game it
launches is a child of the client, not of the command Lutris ran. Its log records both::

    [GameSessionList] {Agent} Reporting process: TrackingInfo(uid=battle.net, type=game, opStatus=On, ...running=4, ...)
    [GameLaunchController] {Main} LaunchBinary: uid=wow selectedRegion=US binaryType=game
    [InstallManager] {Main} Launched C:/Program Files (x86)/World of Warcraft/_retail_/WoW.exe with args: ...
    [GameSessionList] {Agent} Reporting process: TrackingInfo(uid=wow, type=game, opStatus=On, ...running=1, ...)
"""

import glob
import os
import re
import time
from collections.abc import Callable, Iterable

from lutris.util.log import logger

CLIENT_EXES = ("battle.net.exe", "battle.net launcher.exe")
# The processes that stay up while Battle.net runs; the launcher exits once the client starts
CLIENT_PROCESS_NAMES = ("battle.net.exe", "agent.exe")
CLIENT_UID = "battle.net"
ANOTHER_INSTANCE_MESSAGE = "another instance of battle.net is running"

TRACKING_RE = re.compile(r"TrackingInfo\(uid=([^,]+),.*?\brunning=(\d+)")
LAUNCH_RE = re.compile(r"LaunchBinary: uid=(\S+)")
LAUNCHED_EXE_RE = re.compile(r"\] \{Main\} Launched (.+?) with args")

# Longest wait for Battle.net to report itself ready before sending it commands
READY_TIMEOUT_SECONDS = 60
# Extra delay after Battle.net reports itself ready, before it accepts --exec commands
READY_SETTLE_SECONDS = 3
# How long a launched game may take to appear in the log before the launch is given up
GAME_LAUNCH_GRACE_SECONDS = 60


def get_exe_name(exe_path: str) -> str:
    """Return the lower-case file name of a Windows or Unix executable path."""
    return os.path.basename(exe_path.replace("\\", "/")).lower()


def is_battlenet_client(exe_path: str) -> bool:
    """Return whether an executable path points at the Battle.net client."""
    return get_exe_name(exe_path) in CLIENT_EXES


def is_prefix_process_running(prefix_path: str, exe_names: Iterable[str]) -> bool:
    """Return whether a Wine process for one of exe_names (lower-case file names) runs in a prefix.

    Wine shows the Windows path of the program in its command line, and the prefix in
    WINEPREFIX; a process whose environment can't be read is counted as in the prefix."""
    exe_names = set(exe_names)
    prefix = os.path.realpath(prefix_path)
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as cmdline_file:
                args = cmdline_file.read().decode("utf-8", errors="replace").split("\0")[:2]
            if not any(get_exe_name(arg) in exe_names for arg in args):
                continue
            with open("/proc/%s/environ" % pid, "rb") as environ_file:
                environ = environ_file.read().decode("utf-8", errors="replace").split("\0")
        except OSError:
            continue
        wine_prefix = next((var[len("WINEPREFIX=") :] for var in environ if var.startswith("WINEPREFIX=")), None)
        if not wine_prefix or os.path.realpath(wine_prefix) == prefix:
            return True
    return False


def get_log_dirs(prefix_path: str | None) -> list[str]:
    """Return the Battle.net log folders of a Wine prefix. Proton links 'steamuser' to the
    real user folder, so the same folder can show up twice; duplicates are dropped."""
    if not prefix_path:
        return []
    pattern = os.path.join(prefix_path, "drive_c", "users", "*", "AppData", "Local", "Battle.net", "Logs")
    return sorted({os.path.realpath(path) for path in glob.glob(pattern)})


class BattleNetLogWatcher:
    """Reads the lines Battle.net appends to its logs after the watcher was created.

    Battle.net starts a new log file each time it runs, so the log folders are listed
    again on every poll."""

    def __init__(self, prefix_path: str | None) -> None:
        self.prefix_path = prefix_path
        self.client_ready = False
        self.has_new_lines = False
        self.launched_uid: str | None = None
        self.launched_exe: str | None = None
        self._running: dict[str, int] = {}
        self._offsets: dict[str, int] = {}
        for path in self._get_log_files():
            try:
                self._offsets[path] = os.path.getsize(path)
            except OSError as ex:
                logger.debug("Can't read Battle.net log %s: %s", path, ex)

    def _get_log_files(self) -> list[str]:
        """Return the paths of every Battle.net client log in the prefix."""
        log_files = []
        for log_dir in get_log_dirs(self.prefix_path):
            log_files += glob.glob(os.path.join(log_dir, "battle.net-*.log"))
        return log_files

    def _read_new_lines(self) -> list[str]:
        """Return the complete lines written to the logs since they were last read."""
        lines = []
        for path in self._get_log_files():
            offset = self._offsets.get(path, 0)
            try:
                size = os.path.getsize(path)
                if size <= offset:
                    continue
                with open(path, "rb") as log_file:
                    log_file.seek(offset)
                    data = log_file.read(size - offset)
            except OSError as ex:
                logger.debug("Can't read Battle.net log %s: %s", path, ex)
                continue
            # Leave a partly written last line for the next poll
            end = data.rfind(b"\n")
            if end < 0:
                continue
            self._offsets[path] = offset + end + 1
            lines += data[:end].decode("utf-8", errors="replace").splitlines()
        return lines

    def poll(self) -> None:
        """Process the log lines written since the last poll."""
        for line in self._read_new_lines():
            self.has_new_lines = True
            self.parse_line(line)

    def parse_line(self, line: str) -> None:
        """Update what is known about the client and the launched game from one log line."""
        launch_match = LAUNCH_RE.search(line)
        if launch_match:
            # Only the first launch belongs to us; later ones are other games in the same prefix
            if not self.launched_uid:
                self.launched_uid = launch_match.group(1)
            return
        launched_exe_match = LAUNCHED_EXE_RE.search(line)
        if launched_exe_match:
            if self.launched_uid and not self.launched_exe:
                self.launched_exe = launched_exe_match.group(1)
            return
        tracking_match = TRACKING_RE.search(line)
        if tracking_match:
            uid, running = tracking_match.group(1), int(tracking_match.group(2))
            self._running[uid] = running
            if uid == CLIENT_UID:
                # The agent reports the client's own processes once it is up
                self.client_ready = True
            return
        if ANOTHER_INSTANCE_MESSAGE in line:
            # The copy we started found a client that was already running
            self.client_ready = True

    @property
    def game_running(self) -> bool:
        """Whether the game Battle.net launched since the watcher was created is running."""
        return bool(self.launched_uid and self._running.get(self.launched_uid))

    def wait_until_ready(self, quiet_timeout: float) -> None:
        """Block until the log shows Battle.net is up, since it drops --exec commands sent before that.

        Gives up after READY_TIMEOUT_SECONDS, or after quiet_timeout if Battle.net has not
        written to its log at all, which happens when the log is somewhere this doesn't look."""
        logger.info("Waiting for Battle.net to be ready")
        start = time.monotonic()
        while True:
            self.poll()
            if self.client_ready:
                time.sleep(READY_SETTLE_SECONDS)
                logger.info("Battle.net is ready")
                return
            elapsed = time.monotonic() - start
            if elapsed >= READY_TIMEOUT_SECONDS or (not self.has_new_lines and elapsed >= quiet_timeout):
                logger.warning("Battle.net did not report being ready after %d seconds", elapsed)
                return
            time.sleep(1)


class BattleNetGameTracker:
    """Follows one game that Lutris launched through Battle.net, from its log and processes."""

    def __init__(
        self,
        prefix_path: str,
        is_process_running: Callable[[Iterable[str]], bool] | None = None,
    ) -> None:
        self.watcher = BattleNetLogWatcher(prefix_path)
        self.is_process_running = is_process_running or (lambda names: is_prefix_process_running(prefix_path, names))
        self.game_seen = False
        self.launch_start: float | None = None

    def _game_processes_alive(self) -> bool:
        """Whether the game or the client is still running in the prefix. Battle.net writes
        nothing when it is killed, so the log alone would show the game running forever."""
        names = list(CLIENT_PROCESS_NAMES)
        if self.watcher.launched_exe:
            names.append(get_exe_name(self.watcher.launched_exe))
        return self.is_process_running(names)

    def is_alive(self) -> bool:
        """Whether the game is running, or may still be starting."""
        self.watcher.poll()
        if self.watcher.game_running and self._game_processes_alive():
            self.game_seen = True
            return True
        if self.game_seen:
            # The game was running and has now closed
            return False
        if self.launch_start is None:
            self.launch_start = time.monotonic()
        if time.monotonic() - self.launch_start < GAME_LAUNCH_GRACE_SECONDS:
            return True
        logger.warning(
            "Battle.net did not start a game within %d seconds; check that the game code in "
            "'--exec=\"launch <code>\"' is right, the codes are case-sensitive",
            GAME_LAUNCH_GRACE_SECONDS,
        )
        return False


# Games launched through Battle.net, by game config id. They are kept outside the runner,
# because a game rebuilds its runner whenever its configuration is reloaded.
_game_trackers: dict[str, BattleNetGameTracker] = {}


def start_game_tracking(game_config_id: str, prefix_path: str) -> None:
    """Start following a game that is about to be launched through Battle.net."""
    _game_trackers[game_config_id] = BattleNetGameTracker(prefix_path)


def get_game_tracker(game_config_id: str | None) -> BattleNetGameTracker | None:
    """Return the tracker of a game launched through Battle.net, if it is being followed."""
    return _game_trackers.get(game_config_id) if game_config_id else None


def stop_game_tracking(game_config_id: str | None) -> None:
    """Stop following a game."""
    if game_config_id:
        _game_trackers.pop(game_config_id, None)
