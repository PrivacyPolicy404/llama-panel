import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_panel import config as C


class EnvFileTest(unittest.TestCase):
    def test_reads_keys_skips_comments_and_quotes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "paths.env"
            p.write_text("# comentario\nLLAMA_MODELS_DIR = /datos/models\n\nKOBOLD_BIN=\"~/k/kobold\"\nbasura\n")
            self.assertEqual(C._read_env_file(p), {"LLAMA_MODELS_DIR": "/datos/models", "KOBOLD_BIN": "~/k/kobold"})

    def test_missing_file_is_empty(self):
        self.assertEqual(C._read_env_file(Path("/no/existe/paths.env")), {})

    def test_environment_wins_over_file_and_default(self):
        with mock.patch.dict(C._LOCAL, {"X_TEST": "archivo"}, clear=True):
            self.assertEqual(C._env("X_TEST", "def"), "archivo")
            with mock.patch.dict(os.environ, {"X_TEST": "entorno"}):
                self.assertEqual(C._env("X_TEST", "def"), "entorno")
            self.assertEqual(C._env("OTRA_TEST", "def"), "def")

    def test_empty_value_means_default_unless_allowed(self):
        with mock.patch.dict(C._LOCAL, {"X_TEST": ""}, clear=True):
            self.assertEqual(C._env("X_TEST", "/def"), "/def")  # una ruta vacia seria la carpeta actual
            self.assertEqual(C._env("X_TEST", "/def", allow_empty=True), "")


if __name__ == "__main__":
    unittest.main()
