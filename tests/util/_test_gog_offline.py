import os
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from lutris.installer import AUTO_ELF_EXE, AUTO_WIN32_EXE
from lutris.util.gog_offline import (
    GogOfflinePackage,
    bin_volume_stem,
    build_offline_installer,
    filename_name_tokens,
    group_gog_files,
    is_generic_windows_setup,
    parse_innoextract_info,
    resolve_gog_offline_packages,
    scan_gog_offline_directory,
    suggest_package_order,
    suggested_setup_title,
    title_from_filename,
)


def _touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


class TestParseInnoextractInfo(TestCase):
    def test_inspecting_quoted_title_and_gog_id(self):
        output = (
            'Inspecting "The Witcher 3: Wild Hunt" - setup data version 5.6.2 (unicode)\n'
            "Extracting from setup.exe ; 2.1 GiB\n"
            "GOG.com game ID: 1207664663\n"
        )
        title, gogid = parse_innoextract_info(output)
        self.assertEqual(title, "The Witcher 3: Wild Hunt")
        self.assertEqual(gogid, "1207664663")

    def test_gog_id_without_dot_com(self):
        title, gogid = parse_innoextract_info("GOG game ID 1495134320\n")
        self.assertIsNone(title)
        self.assertEqual(gogid, "1495134320")

    def test_bare_numeric_gog_id(self):
        title, gogid = parse_innoextract_info("1207664663")
        self.assertIsNone(title)
        self.assertEqual(gogid, "1207664663")

    def test_empty_output(self):
        self.assertEqual(parse_innoextract_info(""), (None, None))

    def test_inno_title_without_gog_id(self):
        output = 'Inspecting "Example App" - setup data version 5.4.2 (unicode)\nNo GOG.com game ID found!\n'
        title, gogid = parse_innoextract_info(output)
        self.assertEqual(title, "Example App")
        self.assertIsNone(gogid)


class TestFilenameHelpers(TestCase):
    def test_bin_volume_stem(self):
        self.assertEqual(bin_volume_stem("setup_foo_1.0_(99)-1.bin"), "setup_foo_1.0_(99)")
        self.assertEqual(bin_volume_stem("setup_foo_1.0_(99)-12.bin"), "setup_foo_1.0_(99)")
        self.assertIsNone(bin_volume_stem("setup_foo_1.0_(99).exe"))
        self.assertIsNone(bin_volume_stem("readme.bin"))

    def test_name_tokens_strip_version_and_build(self):
        self.assertEqual(
            filename_name_tokens("setup_stellaris_3.8_(12345).exe"),
            ("stellaris",),
        )
        self.assertEqual(
            filename_name_tokens("setup_stellaris_utopia_3.8_(12346).exe"),
            ("stellaris", "utopia"),
        )
        self.assertEqual(
            filename_name_tokens("setup_the_witcher_3_wild_hunt_3.0.0_(12345).exe"),
            ("the", "witcher", "3", "wild", "hunt"),
        )

    def test_title_from_filename(self):
        self.assertEqual(title_from_filename("setup_stellaris_3.8_(1).exe"), "Stellaris")

    def test_generic_windows_setup_accepts_exe_and_msi(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup.exe")
            msi = os.path.join(tmp, "setup.msi")
            volume = os.path.join(tmp, "setup-1.bin")
            script = os.path.join(tmp, "game.sh")
            _touch(exe)
            _touch(msi)
            _touch(volume)
            _touch(script)
            self.assertTrue(is_generic_windows_setup(exe))
            self.assertTrue(is_generic_windows_setup(msi))
            self.assertFalse(is_generic_windows_setup(volume))
            self.assertFalse(is_generic_windows_setup(script))
            self.assertFalse(is_generic_windows_setup(tmp))


class TestGroupGogFiles(TestCase):
    def test_groups_bins_onto_matching_exe(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup_foo_1.0_(99).exe")
            bin1 = os.path.join(tmp, "setup_foo_1.0_(99)-1.bin")
            bin2 = os.path.join(tmp, "setup_foo_1.0_(99)-2.bin")
            dlc = os.path.join(tmp, "setup_foo_bar_1.0_(100).exe")
            dlc_bin = os.path.join(tmp, "setup_foo_bar_1.0_(100)-1.bin")
            orphan = os.path.join(tmp, "setup_missing_1.0_(1)-1.bin")
            for path, size in ((exe, 50), (bin1, 10), (bin2, 10), (dlc, 20), (dlc_bin, 5), (orphan, 8)):
                _touch(path, size)

            packages = group_gog_files([exe, bin1, bin2, dlc, dlc_bin, orphan])
            self.assertEqual(len(packages), 2)
            by_name = {p.filename: p for p in packages}
            self.assertEqual(len(by_name["setup_foo_1.0_(99).exe"].bin_paths), 2)
            self.assertEqual(by_name["setup_foo_1.0_(99).exe"].size, 70)
            self.assertEqual(len(by_name["setup_foo_bar_1.0_(100).exe"].bin_paths), 1)
            self.assertEqual(by_name["setup_foo_1.0_(99).exe"].role, "setup")

    def test_patch_role(self):
        with TemporaryDirectory() as tmp:
            patch = os.path.join(tmp, "patch_foo_1.1_(99).exe")
            _touch(patch, 3)
            packages = group_gog_files([patch])
            self.assertEqual(packages[0].role, "patch")


class TestSuggestPackageOrder(TestCase):
    def _pkg(self, filename, role="setup", directory="/tmp"):
        return GogOfflinePackage(
            exe_path=os.path.join(directory, filename),
            title=filename,
            role=role,
        )

    def test_unique_filename_prefix_is_first(self):
        packages = [
            self._pkg("setup_stellaris_utopia_3.8_(2).exe"),
            self._pkg("setup_stellaris_3.8_(1).exe"),
            self._pkg("patch_stellaris_3.9_(3).exe", role="patch"),
        ]
        ordered = suggest_package_order(packages)
        self.assertEqual(ordered[0].filename, "setup_stellaris_3.8_(1).exe")
        self.assertEqual(ordered[-1].filename, "patch_stellaris_3.9_(3).exe")
        self.assertEqual(ordered[1].filename, "setup_stellaris_utopia_3.8_(2).exe")

    def test_no_unique_prefix_keeps_stable_order(self):
        packages = [
            self._pkg("setup_the_witcher_3_hearts_of_stone_1.0_(1).exe"),
            self._pkg("setup_the_witcher_3_wild_hunt_1.0_(2).exe"),
            self._pkg("setup_the_witcher_3_blood_and_wine_1.0_(3).exe"),
        ]
        ordered = suggest_package_order(packages)
        self.assertEqual([p.filename for p in ordered], [p.filename for p in packages])

    def test_preferred_exe_wins_over_prefix(self):
        packages = [
            self._pkg("setup_stellaris_3.8_(1).exe"),
            self._pkg("setup_stellaris_utopia_3.8_(2).exe"),
        ]
        preferred = packages[1].exe_path
        ordered = suggest_package_order(packages, preferred_path=preferred)
        self.assertEqual(ordered[0].filename, "setup_stellaris_utopia_3.8_(2).exe")

    def test_size_is_not_used(self):
        big_dlc = self._pkg("setup_game_mega_dlc_1.0_(2).exe")
        big_dlc.size = 50_000
        base = self._pkg("setup_game_1.0_(1).exe")
        base.size = 10
        ordered = suggest_package_order([big_dlc, base])
        self.assertEqual(ordered[0].filename, "setup_game_1.0_(1).exe")


class TestScanDirectory(TestCase):
    def test_scan_groups_and_inspects(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup_game_1.0_(1).exe")
            bin1 = os.path.join(tmp, "setup_game_1.0_(1)-1.bin")
            dlc = os.path.join(tmp, "setup_game_dlc_1.0_(2).exe")
            _touch(exe)
            _touch(bin1)
            _touch(dlc)

            def inspect(path):
                if path.endswith("setup_game_1.0_(1).exe"):
                    return "Cool Game", "111"
                return "Cool Game DLC", "222"

            scan = scan_gog_offline_directory(tmp, inspect_fn=inspect)
            packages = scan.packages
            self.assertEqual(len(packages), 2)
            self.assertEqual(packages[0].title, "Cool Game")
            self.assertEqual(packages[0].gogid, "111")
            self.assertEqual(len(packages[0].bin_paths), 1)
            self.assertEqual(packages[1].title, "Cool Game DLC")


class TestResolvePath(TestCase):
    def test_file_scan_uses_parent_directory(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup_game_1.0_(1).exe")
            dlc = os.path.join(tmp, "setup_game_dlc_1.0_(2).exe")
            _touch(exe)
            _touch(dlc)

            def inspect(path):
                if path.endswith("setup_game_1.0_(1).exe"):
                    return "Cool Game", "111"
                return "Cool Game DLC", "222"

            scan = resolve_gog_offline_packages(exe, inspect_fn=inspect)
            self.assertEqual(len(scan.packages), 2)
            self.assertEqual(scan.packages[0].filename, "setup_game_1.0_(1).exe")

    def test_non_gog_exe_is_omitted_without_gog_id(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "SomeInstaller.exe")
            _touch(exe)
            scan = scan_gog_offline_directory(tmp, preferred_path=exe, inspect_fn=lambda _path: (None, None))
            self.assertEqual(scan.packages, [])
            self.assertEqual(scan.suggested_title, "")

    def test_non_gog_inno_title_is_omitted_from_packages(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "Example App 2_1.2.3_Setup.exe")
            _touch(exe)
            scan = scan_gog_offline_directory(
                tmp, preferred_path=exe, inspect_fn=lambda _path: ("Example App", None)
            )
            self.assertEqual(scan.packages, [])
            self.assertEqual(scan.suggested_title, "Example App")

    def test_setup_filename_without_gog_id_or_bins_is_generic(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup_MyApp.exe")
            _touch(exe)
            scan = scan_gog_offline_directory(
                tmp, preferred_path=exe, inspect_fn=lambda _path: ("My App", None)
            )
            self.assertEqual(scan.packages, [])
            self.assertEqual(scan.suggested_title, "My App")

    def test_setup_with_bins_kept_without_gog_id(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup_game_1.0_(1).exe")
            _touch(exe)
            _touch(os.path.join(tmp, "setup_game_1.0_(1)-1.bin"))
            scan = scan_gog_offline_directory(tmp, inspect_fn=lambda _path: ("Cool Game", None))
            self.assertEqual(len(scan.packages), 1)
            self.assertEqual(scan.packages[0].title, "Cool Game")
            self.assertIsNone(scan.packages[0].gogid)

    def test_preferred_generic_ignores_sibling_gog_packages(self):
        with TemporaryDirectory() as tmp:
            generic = os.path.join(tmp, "Example App 2_1.2.3_Setup.exe")
            gog = os.path.join(tmp, "setup_game_1.0_(1).exe")
            _touch(generic)
            _touch(gog)

            def inspect(path):
                if path.endswith("Example App 2_1.2.3_Setup.exe"):
                    return "Example App", None
                return "Cool Game", "111"

            scan = scan_gog_offline_directory(tmp, preferred_path=generic, inspect_fn=inspect)
            self.assertEqual(scan.packages, [])
            self.assertEqual(scan.suggested_title, "Example App")


class TestSuggestedSetupTitle(TestCase):
    def test_uses_innoextract_title_without_gog_id(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "Example App 2_1.2.3_Setup.exe")
            _touch(exe)
            self.assertEqual(suggested_setup_title(exe, inspect_fn=lambda _path: ("Example App", None)), "Example App")

    def test_empty_when_inspect_finds_nothing(self):
        with TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "setup.exe")
            _touch(exe)
            self.assertEqual(suggested_setup_title(exe, inspect_fn=lambda _path: (None, None)), "")

    def test_ignores_non_exe(self):
        with TemporaryDirectory() as tmp:
            msi = os.path.join(tmp, "setup.msi")
            _touch(msi)
            self.assertEqual(suggested_setup_title(msi, inspect_fn=lambda _path: ("Nope", None)), "")
            self.assertEqual(suggested_setup_title(tmp), "")


class TestArchiveCache(TestCase):
    def test_reuses_extract_and_keeps_sibling_dirs(self):
        with TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "game.zip")
            _touch(archive)
            cache_root = os.path.join(tmp, "cache")
            stale = os.path.join(cache_root, "gog-offline", "olduuid")
            os.makedirs(stale)
            _touch(os.path.join(stale, "junk.bin"))

            def fake_extract(_path, dest, merge_single=True):
                _touch(os.path.join(dest, "setup_game_1.0_(1).exe"))

            with patch("lutris.settings.CACHE_DIR", cache_root):
                with patch("lutris.util.extract.extract_archive", side_effect=fake_extract) as extract:
                    first = resolve_gog_offline_packages(archive, inspect_fn=lambda _path: ("Game", "1"))
                    second = resolve_gog_offline_packages(archive, inspect_fn=lambda _path: ("Game", "1"))

            self.assertEqual(len(first.packages), 1)
            self.assertEqual(first.packages[0].exe_path, second.packages[0].exe_path)
            self.assertEqual(extract.call_count, 1)
            self.assertTrue(os.path.isfile(os.path.join(stale, "junk.bin")))
            cached = os.listdir(os.path.join(cache_root, "gog-offline"))
            self.assertEqual(len(cached), 2)

    def test_failed_extract_does_not_leave_dir(self):
        with TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, "game.zip")
            _touch(archive)
            cache_root = os.path.join(tmp, "cache")
            with patch("lutris.settings.CACHE_DIR", cache_root):
                with patch("lutris.util.extract.extract_archive", side_effect=RuntimeError("boom")):
                    with self.assertRaises(RuntimeError) as raised:
                        resolve_gog_offline_packages(archive)
            self.assertIn("Unable to extract", str(raised.exception))
            cache_dir = os.path.join(cache_root, "gog-offline")
            if os.path.isdir(cache_dir):
                self.assertEqual(os.listdir(cache_dir), [])


class TestBuildOfflineInstaller(TestCase):
    def test_windows_chains_autosetup_then_wineexec(self):
        packages = [
            GogOfflinePackage(exe_path="/data/setup_game.exe", title="Game", gogid="111", kind="windows"),
            GogOfflinePackage(exe_path="/data/setup_game_dlc.exe", title="DLC", gogid="222", kind="windows"),
        ]
        installer = build_offline_installer(packages, name="Game", game_slug="game")
        self.assertEqual(installer["runner"], "wine")
        self.assertEqual(installer["gogid"], "111")
        self.assertEqual(installer["game_slug"], "game")
        self.assertEqual(installer["script"]["game"]["exe"], AUTO_WIN32_EXE)
        self.assertEqual(installer["script"]["game"]["arch"], "win64")
        files = installer["script"]["files"]
        self.assertEqual(files[0]["gogsetup0"]["local_path"], "/data/setup_game.exe")
        self.assertTrue(files[0]["gogsetup0"]["url"].startswith("N/A:"))
        steps = installer["script"]["installer"]
        self.assertEqual(steps[0], {"autosetup_gog_game": "gogsetup0"})
        self.assertEqual(steps[1]["task"]["name"], "wineexec")
        self.assertEqual(steps[1]["task"]["executable"], "gogsetup1")
        self.assertEqual(steps[1]["task"]["arch"], "win64")
        self.assertNotIn("gog", installer["version"].lower())

    def test_win32_preset_sets_game_and_wineexec_arch(self):
        packages = [
            GogOfflinePackage(exe_path="/data/setup_game.exe", title="Game", kind="windows"),
            GogOfflinePackage(exe_path="/data/setup_game_dlc.exe", title="DLC", kind="windows"),
        ]
        installer = build_offline_installer(
            packages, name="Game", game_slug="game", wine_arch="win32", win_ver="winxp"
        )
        self.assertEqual(installer["script"]["game"]["arch"], "win32")
        self.assertEqual(installer["script"]["installer"][0]["task"]["arch"], "win32")
        self.assertEqual(installer["script"]["installer"][2]["task"]["arch"], "win32")

    def test_linux_extracts_each_sh(self):
        packages = [
            GogOfflinePackage(exe_path="/data/game.sh", title="Game", kind="linux"),
            GogOfflinePackage(exe_path="/data/dlc.sh", title="DLC", kind="linux"),
        ]
        installer = build_offline_installer(packages, name="Game", game_slug="game")
        self.assertEqual(installer["runner"], "linux")
        self.assertEqual(installer["script"]["game"]["exe"], AUTO_ELF_EXE)
        steps = installer["script"]["installer"]
        self.assertEqual(steps[0]["extract"]["file"], "gogsetup0")
        self.assertEqual(steps[1]["merge"]["dst"], "$GAMEDIR")
        self.assertEqual(steps[2]["extract"]["file"], "gogsetup1")
        self.assertNotIn("wineexec", str(steps))
        self.assertNotIn("autosetup_gog_game", str(steps))

    def test_mixed_linux_and_windows_raise(self):
        packages = [
            GogOfflinePackage(exe_path="/data/game.sh", title="Game", kind="linux"),
            GogOfflinePackage(exe_path="/data/setup_game.exe", title="Game Win", kind="windows"),
        ]
        with self.assertRaises(ValueError):
            build_offline_installer(packages, name="Game", game_slug="game")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            build_offline_installer([], name="x", game_slug="x")


class TestLookupSlug(TestCase):
    @patch("lutris.api.get_api_games", return_value=[{"slug": "the-witcher-3-wild-hunt"}])
    def test_maps_gogid_via_lutris_net(self, _mock):
        from lutris.util.gog_offline import lookup_lutris_slug_for_gogid

        self.assertEqual(lookup_lutris_slug_for_gogid("1207664663"), "the-witcher-3-wild-hunt")

    @patch("lutris.api.get_api_games", side_effect=OSError("offline"))
    def test_lookup_fails_open(self, _mock):
        from lutris.util.gog_offline import lookup_lutris_slug_for_gogid

        self.assertIsNone(lookup_lutris_slug_for_gogid("1207664663"))


class TestInstallerFileLocalPath(TestCase):
    def test_local_path_overrides_dest_and_skips_cache(self):
        from lutris.installer.installer_file import InstallerFile

        installer_file = InstallerFile(
            "game",
            "gogsetup0",
            {
                "url": "N/A:Select the installer from GOG",
                "filename": "setup_game.exe",
                "local_path": "/data/setup_game.exe",
            },
        )
        self.assertTrue(installer_file.is_dest_file_overridden)
        self.assertEqual(installer_file.dest_file, "/data/setup_game.exe")
        self.assertFalse(installer_file.allow_pga_cache)
        self.assertEqual(installer_file.default_provider, "user")
        copied = installer_file.copy()
        self.assertTrue(copied.is_dest_file_overridden)
        self.assertEqual(copied.dest_file, "/data/setup_game.exe")
        self.assertFalse(copied.allow_pga_cache)

    def test_na_without_local_path_is_not_overridden(self):
        from lutris.installer.installer_file import InstallerFile

        installer_file = InstallerFile(
            "game",
            "gogsetup0",
            {
                "url": "N/A:Select the installer from GOG",
                "filename": "setup_game.exe",
            },
        )
        self.assertFalse(installer_file.is_dest_file_overridden)
