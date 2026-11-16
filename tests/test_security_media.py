"""Login dos ficheiros de media: verificado na base de dados uma vez por minuto, não em cada imagem."""

import asyncio
import time
import unittest
from unittest import mock

from app import security


class Req:
    def __init__(self, **session):
        self.session = session


class MediaLogin(unittest.TestCase):
    def setUp(self):
        security._VERIFIED.clear()

    def test_cached_session_skips_database(self):
        security._VERIFIED[(1, 3)] = time.monotonic() + 60
        with mock.patch.object(security, "_check_media_login", side_effect=AssertionError("foi à base de dados")):
            asyncio.run(security.require_login_media(Req(cred_id=1, ver=3)))

    def test_unknown_or_expired_session_checks_database(self):
        calls = []
        with mock.patch.object(security, "_check_media_login", side_effect=lambda r: calls.append(r.session)):
            asyncio.run(security.require_login_media(Req(cred_id=1, ver=3)))
            security._VERIFIED[(1, 4)] = time.monotonic() - 1  # expirada
            asyncio.run(security.require_login_media(Req(cred_id=1, ver=4)))
            asyncio.run(security.require_login_media(Req()))  # sem sessão: a verificação é que decide (LoginRequired)
        self.assertEqual(len(calls), 3)

    def test_forget_verified(self):
        security._VERIFIED[(1, 3)] = time.monotonic() + 60
        security.forget_verified()
        self.assertFalse(security._verified(Req(cred_id=1, ver=3)))
