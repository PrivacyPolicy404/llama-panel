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
        # mismo resultado efectivo: otro orden, o el valor global repetido en el modelo
        self.assertEqual(base, PC.file_name("modelo-a", PRESET.replace("model = /x/a.gguf\ncache-type-k = q8_0",
                                                                      "cache-type-k = q8_0\nmodel = /x/a.gguf")))
        self.assertEqual(base, PC.file_name("modelo-a", PRESET.replace("cache-type-k = q8_0",
                                                                      "cache-type-k = q8_0\nctx-size = 8192")))
        # el modelo pisa al global: el resultado efectivo cambia
        self.assertNotEqual(base, PC.file_name("modelo-a", PRESET.replace("cache-type-k = q8_0",
                                                                         "cache-type-k = q8_0\nctx-size = 4096")))


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


class Profiles(unittest.TestCase):
    def test_slug_and_names(self):
        self.assertEqual(PC.slug(" Voz Rápida! "), "voz-r-pida")
        self.assertEqual(PC.slug(""), PC.MAIN)
        main, voz = PC.file_name("modelo-a", PRESET), PC.file_name("modelo-a", PRESET, "voz")
        self.assertRegex(main, r"^modelo-a\.[0-9a-f]{10}\.bin$")  # el principal mantiene el nombre de siempre
        self.assertEqual(voz, main.replace(".bin", ".voz.bin"))
        self.assertEqual(PC._parse("modelo-a", voz)[1], "voz")
        self.assertIsNone(PC._parse("modelo-a", PC.file_name("modelo-a-hcq8", PRESET)))

    def test_list_and_delete_one_profile(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(C, "SLOTS_DIR", Path(d)), \
                mock.patch.object(PC, "config_hash", lambda mid, text=None: "0123456789"):
            for prof, tok in ((PC.MAIN, 7101), ("voz", 2000), ("cline", 6900)):
                f = Path(d, PC.file_name("modelo-a", profile=prof))
                f.write_text("x")
                f.with_suffix(".json").write_text('{"tokens": %d}' % tok)
            Path(d, "modelo-a.abcdefabcd.voz.bin").write_text("otra configuración")
            names = [p["name"] for p in PC.profiles("modelo-a")]
            self.assertEqual(names[0], PC.MAIN)
            self.assertEqual(sorted(names), ["cline", PC.MAIN, "voz"])
            self.assertEqual(PC.delete("modelo-a", "voz"), 1)
            self.assertTrue(Path(d, "modelo-a.abcdefabcd.voz.bin").exists())  # de otra configuración: no se toca
            self.assertEqual(sorted(p["name"] for p in PC.profiles("modelo-a")), ["cline", PC.MAIN])

    def test_prime_leaves_the_main_profile_in_the_slot(self):
        calls = []
        found = [{"name": PC.MAIN}, {"name": "voz"}, {"name": "cline"}]
        with mock.patch.object(PC, "profiles", lambda mid: found), \
                mock.patch.object(PC, "_slot", lambda mid: {"id_task": -1}), \
                mock.patch.object(PC, "restore", lambda mid, profile: (calls.append(("restore", profile)), (100, None))[1]), \
                mock.patch.object(PC, "_nudge", lambda mid: calls.append(("nudge",))):
            done = PC.prime("modelo-a")
        self.assertEqual(calls, [("restore", "voz"), ("nudge",), ("restore", "cline"), ("nudge",), ("restore", PC.MAIN)])
        self.assertEqual([p for p, _ in done], ["voz", "cline", PC.MAIN])
        with mock.patch.object(PC, "profiles", lambda mid: found), mock.patch.object(PC, "_slot", lambda mid: {"id_task": 7}):
            self.assertEqual(PC.prime("modelo-a"), [])  # el slot ya atendió algo: no se pisa
