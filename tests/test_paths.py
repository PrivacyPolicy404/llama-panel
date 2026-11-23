"""Rutas configurables desde el panel: validación, paths.env, conteo de modelos y sugerencias."""
import os
import tempfile
import unittest
from pathlib import Path

from llama_panel import paths


def vals(**kw):
    base = {"text_dirs": ["/datos/models"], "img_dir": "", "llama_server": "/opt/llama-server", "kobold": "",
            "gpu_name": ""}
    return {**base, **kw}


class Validate(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(paths.validate(vals()), [])
        self.assertEqual(paths.validate(vals(text_dirs=["~/models", "/mnt/con espacio"])), [])
        self.assertEqual(paths.validate(vals(kwh_price="95,5")), [])

    def test_rejects(self):
        self.assertTrue(paths.validate(vals(text_dirs=[])))
        self.assertTrue(paths.validate(vals(text_dirs=["relativa/models"])))
        self.assertTrue(paths.validate(vals(text_dirs=["/a:b"])))
        self.assertTrue(paths.validate(vals(llama_server="/opt/con espacio/llama-server")))
        self.assertTrue(paths.validate(vals(llama_server="")))
        self.assertTrue(paths.validate(vals(gpu_name="RX\n580")))
        self.assertTrue(paths.validate(vals(kwh_price="caro")))
        self.assertTrue(paths.validate(vals(kwh_price="-3")))
        self.assertTrue(paths.validate(vals(img_dir="/x\nLLAMA_SERVER_BIN=/evil")))


class EnvFile(unittest.TestCase):
    def test_new_file(self):
        text = paths.env_lines(vals(text_dirs=["/a", "/b", "/c"], gpu_name="RX 580"))
        self.assertIn("LLAMA_MODELS_DIR=/a\n", text)
        self.assertIn("LLAMA_EXTRA_MODELS_DIRS=/b:/c\n", text)
        self.assertIn("LLAMA_GPU_NAME=RX 580\n", text)

    def test_keeps_comments_order_and_unknown_keys(self):
        old = "# mis rutas\nLLAMA_MODELS_DIR=/viejo\nOTRA=1\nLLAMA_MODELS_DIR=/duplicada\n"
        text = paths.env_lines(vals(), old)
        lines = text.splitlines()
        self.assertEqual(lines[:3], ["# mis rutas", "LLAMA_MODELS_DIR=/datos/models", "OTRA=1"])
        self.assertNotIn("/duplicada", text)
        self.assertIn("LLAMA_EXTRA_MODELS_DIRS=\n", text)  # vacía: sin carpetas extra (no vuelve el valor por defecto)


class Scan(unittest.TestCase):
    def test_counts_models_like_the_panel(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("a.gguf", "sub/b-00001-of-00002.gguf", "sub/b-00002-of-00002.gguf", "mmproj-x.gguf",
                         "mtp-y.gguf", "notas.txt", ".oculta/z.gguf"):
                p = Path(d) / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("")
            self.assertEqual(paths.count_files(d, paths.is_text_model), (2, False))
            check = paths.check(vals(text_dirs=[d, d + "/no-existe"]))["text_dirs"]
            self.assertEqual([c["ok"] for c in check], [True, False])

    def test_walk_limit(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(30):
                (Path(d) / f"m{i}.gguf").write_text("")
            old, paths.WALK_LIMIT = paths.WALK_LIMIT, 10
            try:
                self.assertEqual(paths.count_files(d, paths.is_text_model), (30, True))
            finally:
                paths.WALK_LIMIT = old

    def test_optional_binaries(self):
        c = paths.check(vals(kobold=""))
        self.assertIsNone(c["kobold"]["ok"])
        self.assertFalse(c["llama_server"]["ok"])  # /opt/llama-server no existe
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(paths._bin_check(f.name)["msg"], "no es un ejecutable")

    def test_suggest_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("models", "modelos-img", ".cache", "otra"):
                (Path(d) / name).mkdir()
            (Path(d) / "model-archivo").write_text("")
            self.assertEqual(paths.suggest_dirs(d + "/mod"), [d + "/modelos-img/", d + "/models/"])
            self.assertEqual(len(paths.suggest_dirs(d + "/")), 3)  # sin la oculta ni el archivo
            self.assertEqual(paths.suggest_dirs("relativa"), [])


if __name__ == "__main__":
    unittest.main()
