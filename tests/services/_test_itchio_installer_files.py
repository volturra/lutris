"""itch.io installer files fall back to a user-provided file when the game can't be fetched."""

import unittest
from unittest.mock import Mock, patch

from lutris.exceptions import UnavailableGameError
from lutris.services.itchio import ItchIoService
from lutris.util.http import HTTPError, UnauthorizedAccessError


class TestGetInstallerFiles(unittest.TestCase):
    def setUp(self):
        self.service = ItchIoService()

    def test_missing_game_id_is_unavailable(self):
        installer = Mock(service_appid=None)
        with self.assertRaises(UnavailableGameError):
            self.service.get_installer_files(installer, "installer")

    def test_not_logged_in_is_unavailable(self):
        installer = Mock(service_appid="123")
        with patch.object(ItchIoService, "get_key", side_effect=UnauthorizedAccessError("no")):
            with self.assertRaises(UnavailableGameError):
                self.service.get_installer_files(installer, "installer")

    def test_http_error_is_unavailable(self):
        installer = Mock(service_appid="123")
        with (
            patch.object(ItchIoService, "get_key", return_value=None),
            patch.object(ItchIoService, "fetch_uploads", side_effect=HTTPError("boom")),
        ):
            with self.assertRaises(UnavailableGameError):
                self.service.get_installer_files(installer, "installer")
