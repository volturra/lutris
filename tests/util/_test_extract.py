"""Tests for archive extraction."""

import io
import os
import tarfile
import zipfile
from tempfile import TemporaryDirectory
from unittest import TestCase

from lutris.util.extract import ExtractError, extract_archive


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def make_tgz(path, name, data):
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class TestNestedArchives(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = self._tmp.name
        self.dest = os.path.join(self.tmp, "dest")
        os.mkdir(self.dest)

    def tearDown(self):
        self._tmp.cleanup()

    def test_inner_archive_is_kept_by_default(self):
        """A Humble zip holding a MojoSetup installer must leave the installer for a later step."""
        # MojoSetup installers are a shell script with a zip appended; zipfile still detects them
        installer = b"#!/bin/sh\nexit 0\n" + zip_bytes({"data/game.bin": b"game"})
        outer = os.path.join(self.tmp, "Game-Installer_Humble_Linux.zip")
        make_zip(outer, {"Game-Installer": installer})

        extract_archive(outer, self.dest)

        self.assertEqual(os.listdir(self.dest), ["Game-Installer"])
        with open(os.path.join(self.dest, "Game-Installer"), "rb") as inner:
            self.assertEqual(inner.read(), installer)

    def test_inner_archive_is_extracted_when_nested(self):
        """GameJolt wraps its builds in a tar.gz around the real archive."""
        outer = os.path.join(self.tmp, "gamejoltbuild.tar.gz")
        make_tgz(outer, "build.zip", zip_bytes({"game/run.sh": b"#!/bin/sh\n"}))

        extract_archive(outer, self.dest, extract_nested=True)

        self.assertEqual(os.listdir(self.dest), ["run.sh"])


class TestUnsafeTarMembers(TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = self._tmp.name
        self.dest = os.path.join(self.tmp, "dest")
        os.mkdir(self.dest)

    def tearDown(self):
        self._tmp.cleanup()

    def make_tar(self, members):
        path = os.path.join(self.tmp, "archive.tar.gz")
        with tarfile.open(path, "w:gz") as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def file_member(self, name, data=b"data"):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, data

    def symlink_member(self, name, target):
        info = tarfile.TarInfo(name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        return info, None

    def test_member_with_an_absolute_path_stays_in_the_destination(self):
        escaped = os.path.join(self.tmp, "escaped.txt")
        archive = self.make_tar([self.file_member(escaped)])
        extract_archive(archive, self.dest)
        self.assertFalse(os.path.exists(escaped))

    def test_symlink_pointing_outside_the_destination_is_refused(self):
        archive = self.make_tar([self.symlink_member("game/link", "/etc")])
        with self.assertRaises(ExtractError):
            extract_archive(archive, self.dest)

    def test_relative_symlink_inside_the_archive_is_kept(self):
        archive = self.make_tar(
            [
                self.file_member("game/lib/libfoo.so.1"),
                self.symlink_member("game/lib/libfoo.so", "libfoo.so.1"),
            ]
        )
        extract_archive(archive, self.dest)
        link = os.path.join(self.dest, "lib", "libfoo.so")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), "libfoo.so.1")


class TestFailedExtraction(TestCase):
    """7-Zip's exit status must be checked, or a damaged archive installs only partly."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dest = os.path.join(self._tmp.name, "dest")
        os.mkdir(self.dest)
        # Random data doesn't compress, so cutting the archive short cuts off whole files
        self.files = {"first.bin": os.urandom(100_000), "second.bin": os.urandom(100_000)}
        self.archive = os.path.join(self._tmp.name, "game.zip")
        make_zip(self.archive, self.files)

    def test_truncated_archive_is_an_error(self):
        with open(self.archive, "rb") as archive_file:
            data = archive_file.read()
        with open(self.archive, "wb") as archive_file:
            archive_file.write(data[: len(data) * 3 // 4])
        with self.assertRaises(ExtractError):
            extract_archive(self.archive, self.dest)
        self.assertEqual(os.listdir(self.dest), [])

    def test_file_that_is_not_an_archive_is_an_error(self):
        with open(self.archive, "wb") as archive_file:
            archive_file.write(os.urandom(10_000))
        with self.assertRaises(ExtractError):
            extract_archive(self.archive, self.dest)
        self.assertEqual(os.listdir(self.dest), [])

    def test_exe_that_is_not_an_archive_is_an_error(self):
        setup_path = os.path.join(self._tmp.name, "setup.exe")
        with open(setup_path, "wb") as setup_file:
            setup_file.write(os.urandom(10_000))
        with self.assertRaises(ExtractError):
            extract_archive(setup_path, self.dest)
        self.assertEqual(os.listdir(self.dest), [])

    def test_data_after_the_archive_is_tolerated(self):
        with open(self.archive, "ab") as archive_file:
            archive_file.write(os.urandom(5_000))
        extract_archive(self.archive, self.dest, merge_single=False)
        self.assertEqual(sorted(os.listdir(self.dest)), sorted(self.files))
