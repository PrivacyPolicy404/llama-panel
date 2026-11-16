"""Prompt inicial guardado: que opciones del preset cuentan para el nombre y que archivos son de cada modelo."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llama_panel import config as C
from llama_panel import prompt_cache as PC

PRESET = """# comentario
[*]
ctx-size = 8192

[modelo-a]
model = /x/a.gguf
cache-type-k = q8_0

[modelo-a-hcq8]
model = /x/b.gguf
"""


class Names(unittest.TestCase):
    def test_block_has_global_and_own_section_only(self):
        self.assertEqual(PC.preset_block(PRESET, "modelo-a"),
                         ["*:ctx-size = 8192", "modelo-a:model = /x/a.gguf", "modelo-a:cache-type-k = q8_0"])

    def test_name_changes_with_the_model_options_and_not_with_other_models(self):
        base = PC.file_name("modelo-a", PRESET)
        self.assertRegex(base, r"^modelo-a\.[0-9a-f]{10}\.bin$")
        self.assertNotEqual(base, PC.file_name("modelo-a", PRESET.replace("q8_0", "f16")))
        self.assertNotEqual(base, PC.file_name("modelo-a", PRESET.replace("8192", "65536")))
        self.assertEqual(base, PC.file_name("modelo-a", PRESET.replace("/x/b.gguf", "/x/c.gguf")))
        self.assertEqual(base, PC.file_name("modelo-a", PRESET.replace("# comentario", "# otro")))


class Files(unittest.TestCase):
    def test_delete_only_touches_that_model(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, "SLOTS_DIR", Path(d)):
            for name in ("modelo-a.0123456789.bin", "modelo-a.0123456789.json", "modelo-a.abcdef0123.bin",
                         "modelo-a-hcq8.0123456789.bin", "modelo-a.5-9B.0123456789.bin", "modelo-a.notas.bin"):
                Path(d, name).write_text("x")
            self.assertEqual(PC.delete("modelo-a"), 2)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()),
                             ["modelo-a-hcq8.0123456789.bin", "modelo-a.5-9B.0123456789.bin", "modelo-a.notas.bin"])

    def test_info_none_without_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, "SLOTS_DIR", Path(d)), \
                mock.patch.object(C, "PRESET_FILE", Path(d, "presets.ini")):
            self.assertIsNone(PC.info("modelo-a"))
            Path(d, "presets.ini").write_text(PRESET)
            Path(d, PC.file_name("modelo-a")).write_text("xxxx")
            Path(d, PC.file_name("modelo-a")).with_suffix(".json").write_text('{"tokens": 6932}')
            got = PC.info("modelo-a")
            self.assertEqual((got["tokens"], got["bytes"]), (6932, 4))


if __name__ == "__main__":
    unittest.main()
