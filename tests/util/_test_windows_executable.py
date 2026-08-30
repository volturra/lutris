import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris.util.windows_executable import (
    KIND_GAME,
    KIND_INSTALLER,
    KIND_UNKNOWN,
    classify_windows_executable,
    find_installed_game_for_path,
    first_local_path_from_args,
    has_adjacent_bin_volumes,
    has_installer_filename,
    has_installer_pe_signature,
    is_lutris_installer_script,
    is_windows_executable,
    looks_like_local_file_arg,
    paths_refer_to_same_file,
    resolve_local_file_arg,
    suggested_game_name,
)


def _touch(path: str, data: bytes = b"MZ") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class TestLooksLikeLocalFile(TestCase):
    def test_lutris_uris_are_not_local_files(self):
        self.assertFalse(looks_like_local_file_arg("lutris:quake"))
        self.assertFalse(looks_like_local_file_arg("lutris:rungameid/12"))
        self.assertIsNone(first_local_path_from_args(["lutris:quake"]))

    def test_absolute_and_file_uris(self):
        self.assertTrue(looks_like_local_file_arg("/tmp/game.exe"))
        self.assertTrue(looks_like_local_file_arg("file:///tmp/game.exe"))
        self.assertEqual(resolve_local_file_arg("file:///tmp/game.exe"), "/tmp/game.exe")
        self.assertEqual(resolve_local_file_arg("file://localhost/tmp/game.exe"), "/tmp/game.exe")

    def test_skips_lutris_binary_and_uses_the_exe(self):
        self.assertIsNone(first_local_path_from_args(["/opt/lutris/bin/lutris"]))
        self.assertEqual(
            first_local_path_from_args(
                [
                    "/usr/bin/python3",
                    "/opt/lutris/bin/lutris",
                    "/tmp/game.exe",
                ],
            ),
            "/tmp/game.exe",
        )

    def test_relative_exe_with_cwd(self):
        self.assertEqual(
            resolve_local_file_arg("game.exe", cwd="/games/foo"),
            "/games/foo/game.exe",
        )

    def test_installer_script_extensions(self):
        self.assertTrue(is_lutris_installer_script("/tmp/script.lutris"))
        self.assertTrue(is_lutris_installer_script("/tmp/script.yml"))
        self.assertTrue(looks_like_local_file_arg("script.yaml"))


class TestInstallerHeuristics(TestCase):
    def test_installer_filenames(self):
        self.assertTrue(has_installer_filename("/tmp/setup.exe"))
        self.assertTrue(has_installer_filename("/tmp/setup_coolgame_3.8_(12345).exe"))
        self.assertTrue(has_installer_filename("/tmp/game-setup.exe"))
        self.assertTrue(has_installer_filename("/tmp/Example App 2_1.2.3_Setup.exe"))
        self.assertTrue(has_installer_filename("/tmp/Installer.exe"))
        self.assertFalse(has_installer_filename("/tmp/Game.exe"))
        self.assertFalse(has_installer_filename("/tmp/unins000.exe"))

    def test_pe_signature_and_bin_volumes(self):
        with TemporaryDirectory() as tmp:
            setup = _touch(
                os.path.join(tmp, "setup_foo.exe"),
                b"MZ" + b"\x00" * 64 + b"NullsoftInst",
            )
            _touch(os.path.join(tmp, "setup_foo-1.bin"), b"bin")
            self.assertTrue(has_installer_pe_signature(setup))
            self.assertTrue(has_adjacent_bin_volumes(setup))
            self.assertEqual(classify_windows_executable(setup), KIND_INSTALLER)

    def test_gog_com_string_is_not_an_installer_signature(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "Game.exe"), b"MZ" + b"\x00" * 64 + b"GOG.com")
            self.assertFalse(has_installer_pe_signature(exe))
            self.assertEqual(classify_windows_executable(exe), KIND_UNKNOWN)

    def test_numeric_bin_next_to_game_exe_is_not_installer(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "CoolGame.exe"))
            _touch(os.path.join(tmp, "CoolGame-1.bin"), b"bin")
            _touch(os.path.join(tmp, "CoolGame-data.bin"), b"bin")
            self.assertTrue(has_adjacent_bin_volumes(exe))
            self.assertEqual(classify_windows_executable(exe), KIND_UNKNOWN)

    def test_plain_game_exe_is_unknown_outside_prefix(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "CoolGame.exe"))
            self.assertEqual(classify_windows_executable(exe), KIND_UNKNOWN)

    def test_exe_inside_wine_prefix_is_a_game(self):
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c", "Games", "Foo"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            exe = _touch(os.path.join(tmp, "drive_c", "Games", "Foo", "Foo.exe"))
            self.assertEqual(classify_windows_executable(exe), KIND_GAME)

    def test_pe_signature_inside_prefix_is_still_a_game(self):
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c", "Games", "Foo"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            exe = _touch(
                os.path.join(tmp, "drive_c", "Games", "Foo", "Foo.exe"),
                b"MZ" + b"\x00" * 64 + b"NullsoftInst",
            )
            self.assertTrue(has_installer_pe_signature(exe))
            self.assertEqual(classify_windows_executable(exe), KIND_GAME)

    def test_setup_inside_prefix_is_ambiguous(self):
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c", "Games", "Foo"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            exe = _touch(os.path.join(tmp, "drive_c", "Games", "Foo", "setup.exe"))
            self.assertEqual(classify_windows_executable(exe), KIND_UNKNOWN)

    def test_named_setup_outside_drive_c_is_installer(self):
        """An ancestor prefix (including $HOME) must not hide a downloaded installer."""
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            downloads = os.path.join(tmp, "tmp")
            os.makedirs(downloads)
            exe = _touch(os.path.join(downloads, "Example App 2_1.2.3_Setup.exe"))
            self.assertEqual(classify_windows_executable(exe), KIND_INSTALLER)

    def test_setup_next_to_proton_pfx_is_installer(self):
        with TemporaryDirectory() as tmp:
            pfx = os.path.join(tmp, "pfx")
            os.makedirs(os.path.join(pfx, "drive_c"))
            open(os.path.join(pfx, "user.reg"), "w", encoding="utf-8").close()
            exe = _touch(os.path.join(tmp, "Game_Setup.exe"))
            self.assertEqual(classify_windows_executable(exe), KIND_INSTALLER)

    def test_msi_and_folder_are_installers(self):
        with TemporaryDirectory() as tmp:
            msi = _touch(os.path.join(tmp, "game.msi"))
            self.assertEqual(classify_windows_executable(msi), KIND_INSTALLER)
            self.assertEqual(classify_windows_executable(tmp), KIND_INSTALLER)

    def test_suggested_name_from_gog_setup(self):
        self.assertEqual(
            suggested_game_name("/tmp/setup_coolgame_3.8_(12345).exe"),
            "Coolgame",
        )


class TestLibraryLookup(TestCase):
    def test_same_file_paths(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "Foo.exe"))
            self.assertTrue(paths_refer_to_same_file(exe, exe))
            self.assertTrue(paths_refer_to_same_file(exe, os.path.join(tmp, ".", "Foo.exe")))

    def test_path_cache_hit(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "Foo.exe"))
            db_game = {"id": "12", "installed": 1, "name": "Foo", "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={"12": exe}),
                patch("lutris.database.games.get_game_by_field", return_value=db_game),
            ):
                self.assertEqual(find_installed_game_for_path(exe), db_game)

    def test_unique_directory_match(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "Launcher.exe"))
            db_game = {"id": "9", "installed": 1, "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={}),
                patch("lutris.database.games.get_games", return_value=[db_game]),
            ):
                self.assertEqual(find_installed_game_for_path(exe), db_game)

    def test_directory_match_skips_installer_filename(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "setup.exe"))
            db_game = {"id": "9", "installed": 1, "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={}),
                patch("lutris.database.games.get_games", return_value=[db_game]),
            ):
                self.assertIsNone(find_installed_game_for_path(exe))

    def test_directory_match_ignores_files_outside_prefix_drive_c(self):
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c", "Games", "Foo"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            downloads = os.path.join(tmp, "tmp")
            os.makedirs(downloads)
            exe = _touch(os.path.join(downloads, "Foo.exe"))
            db_game = {"id": "9", "installed": 1, "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={}),
                patch("lutris.database.games.get_games", return_value=[db_game]),
            ):
                self.assertIsNone(find_installed_game_for_path(exe))

    def test_directory_match_inside_prefix_drive_c(self):
        with TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "drive_c", "Games", "Foo"))
            open(os.path.join(tmp, "user.reg"), "w", encoding="utf-8").close()
            exe = _touch(os.path.join(tmp, "drive_c", "Games", "Foo", "Foo.exe"))
            db_game = {"id": "9", "installed": 1, "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={}),
                patch("lutris.database.games.get_games", return_value=[db_game]),
            ):
                self.assertEqual(find_installed_game_for_path(exe), db_game)

    def test_path_cache_still_matches_installer_named_exe(self):
        with TemporaryDirectory() as tmp:
            exe = _touch(os.path.join(tmp, "setup.exe"))
            db_game = {"id": "12", "installed": 1, "name": "Foo", "directory": tmp}
            with (
                patch("lutris.util.path_cache.read_path_cache", return_value={"12": exe}),
                patch("lutris.database.games.get_game_by_field", return_value=db_game),
            ):
                self.assertEqual(find_installed_game_for_path(exe), db_game)

    def test_windows_executable_helper(self):
        self.assertTrue(is_windows_executable("/tmp/a.exe"))
        self.assertTrue(is_windows_executable("/tmp/a.msi"))
        self.assertFalse(is_windows_executable("/tmp/a.sh"))
