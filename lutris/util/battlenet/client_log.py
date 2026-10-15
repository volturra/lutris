"""Follow the Battle.net client's own log to learn when it is ready and which games it runs.

Battle.net ignores ``--exec="launch <code>"`` while it is still starting up, and a game it
launches is a child of the client, not of the command Lutris ran. Its log records both::

    [GameSessionList] {Agent} Reporting process: TrackingInfo(uid=battle.net, type=game, opStatus=On, ...running=4, ...)
    [GameLaunchController] {Main} LaunchBinary: uid=wow selectedRegion=US binaryType=game
    [GameSessionList] {Agent} Reporting process: TrackingInfo(uid=wow, type=game, opStatus=On, ...running=1, ...)
"""

import glob
import os
import re

CLIENT_EXES = ("battle.net.exe", "battle.net launcher.exe")
CLIENT_UID = "battle.net"
ANOTHER_INSTANCE_MESSAGE = "another instance of battle.net is running"

TRACKING_RE = re.compile(r"TrackingInfo\(uid=([^,]+),.*?\brunning=(\d+)")
LAUNCH_RE = re.compile(r"LaunchBinary: uid=(\S+)")


def is_battlenet_client(exe_path: str) -> bool:
    """Return whether an executable path points at the Battle.net client."""
    return os.path.basename(exe_path.replace("\\", "/")).lower() in CLIENT_EXES


def get_log_dirs(prefix_path: str) -> list[str]:
    """Return the Battle.net log folders of a Wine prefix. Proton links 'steamuser' to the
    real user folder, so the same folder can show up twice; duplicates are dropped."""
    pattern = os.path.join(prefix_path, "drive_c", "users", "*", "AppData", "Local", "Battle.net", "Logs")
    return sorted({os.path.realpath(path) for path in glob.glob(pattern)})


class BattleNetLogWatcher:
    """Reads the lines Battle.net appends to its logs after the watcher was created."""

    def __init__(self, prefix_path: str) -> None:
        self.prefix_path = prefix_path
        self.client_ready = False
        self.launched_uid: str | None = None
        self.running: dict[str, int] = {}
        self._offsets: dict[str, int] = {}
        for path in self._get_log_files():
            try:
                self._offsets[path] = os.path.getsize(path)
            except OSError:
                pass

    def _get_log_files(self) -> list[str]:
        log_files = []
        for log_dir in get_log_dirs(self.prefix_path):
            log_files += glob.glob(os.path.join(log_dir, "battle.net-*.log"))
        return log_files

    def _read_new_lines(self) -> list[str]:
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
            except OSError:
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
            self.parse_line(line)

    def parse_line(self, line: str) -> None:
        launch_match = LAUNCH_RE.search(line)
        if launch_match:
            self.launched_uid = launch_match.group(1)
            return
        tracking_match = TRACKING_RE.search(line)
        if tracking_match:
            uid, running = tracking_match.group(1), int(tracking_match.group(2))
            self.running[uid] = running
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
        return bool(self.launched_uid and self.running.get(self.launched_uid))
