"""Contraseña del panel: sin contraseña pasa todo; con contraseña, sesión, token o pedido local."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_panel import auth


class Auth(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = mock.patch.object(auth, "AUTH_FILE", Path(d.name) / "auth.json")
        p.start()
        self.addCleanup(p.stop)
        auth._fails.clear()

    def test_open_without_password(self):
        self.assertFalse(auth.enabled())
        self.assertTrue(auth.allowed("192.168.1.5", {}))

    def test_password_session_and_local(self):
        self.assertIsNotNone(auth.set_password("corta"))  # menos de 8 caracteres
        self.assertIsNone(auth.set_password("una clave larga"))
        self.assertFalse(auth.allowed("192.168.1.5", {}))
        self.assertTrue(auth.allowed("127.0.0.1", {}))  # esta máquina y el túnel SSH
        self.assertTrue(auth.allowed("::1", {}))
        self.assertFalse(auth.check_password("otra"))
        sid = auth.new_session()
        self.assertTrue(auth.allowed("192.168.1.5", {"Cookie": f"x=1; {auth.COOKIE}={sid}"}))
        self.assertFalse(auth.allowed("192.168.1.5", {"Cookie": f"{auth.COOKIE}=inventada"}))
        auth.end_session(sid)
        self.assertFalse(auth.allowed("192.168.1.5", {"Cookie": f"{auth.COOKIE}={sid}"}))

    def test_changing_needs_the_current_one_and_closes_sessions(self):
        auth.set_password("primera clave")
        sid = auth.new_session()
        self.assertIsNotNone(auth.set_password("segunda clave", current="mal"))
        self.assertIsNone(auth.set_password("segunda clave", current="primera clave"))
        self.assertFalse(auth.allowed("10.0.0.2", {"Cookie": f"{auth.COOKIE}={sid}"}))
        self.assertIsNone(auth.set_password("", current="segunda clave"))  # sacarla
        self.assertTrue(auth.allowed("10.0.0.2", {}))

    def test_tokens_and_only_hashes_on_disk(self):
        auth.set_password("una clave larga")
        token, err = auth.new_token("laptop")
        self.assertIsNone(err)
        self.assertTrue(auth.allowed("10.0.0.2", {"Authorization": f"Bearer {token}"}))
        self.assertFalse(auth.allowed("10.0.0.2", {"Authorization": "Bearer lp_otro"}))
        disk = auth.AUTH_FILE.read_text()
        self.assertNotIn(token, disk)
        self.assertNotIn("una clave larga", disk)
        self.assertEqual(auth.AUTH_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual([t["name"] for t in auth.tokens()], ["laptop"])
        self.assertTrue(auth.revoke_token("laptop"))
        self.assertFalse(auth.allowed("10.0.0.2", {"Authorization": f"Bearer {token}"}))
        self.assertEqual(auth.new_token("  ")[0], None)

    def test_failed_logins_slow_down(self):
        self.assertEqual(auth.pending_delay("10.0.0.9"), 0)
        delays = [auth.throttle("10.0.0.9", False) for _ in range(6)]
        self.assertEqual(delays, [0.5, 1, 2, 4, 8, 10])
        self.assertGreater(auth.pending_delay("10.0.0.9"), 9)
        auth.throttle("10.0.0.9", True)
        self.assertEqual(auth.pending_delay("10.0.0.9"), 0)


if __name__ == "__main__":
    unittest.main()
