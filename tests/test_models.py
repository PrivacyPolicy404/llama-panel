"""Escaneo de modelos, generacion de presets.ini / argumentos de KoboldCpp y validacion de ajustes."""
import os
import tempfile
import unittest
from pathlib import Path

from llama_panel import config as C
from llama_panel import img_models as IM
from llama_panel import text_models as TM

MB = 1024 * 1024


def make(root, rel, size_mb):
    p = Path(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.truncate(size_mb * MB)  # archivo disperso: no ocupa disco
    return p


def settings(**kw):
    return {"global": "n-gpu-layers = 99", "models": {}, "image": {"selected": "", "models": {}}, **kw}


class CleanLines(unittest.TestCase):
    def test_valid_invalid_and_comments(self):
        ok, bad = C.clean_lines("ctx-size=8192\n# comentario\n\n  parallel =  1 \nesto no\n=sinclave")
        self.assertEqual(ok, ["ctx-size = 8192", "parallel = 1"])
        self.assertEqual(bad, ["esto no", "=sinclave"])


class TextScan(unittest.TestCase):
    def test_scan_skips_small_mmproj_and_dedupes_ids(self):
        with tempfile.TemporaryDirectory() as d:
            make(d, "a/model-Q4.gguf", 60)
            make(d, "b/model-Q4.gguf", 60)
            make(d, "mmproj-model.gguf", 60)
            make(d, "tiny.gguf", 1)
            os.symlink(Path(d, "a/model-Q4.gguf"), Path(d, "link.gguf"))
            models = TM.scan_models([d])
        self.assertEqual([m["id"] for m in models], ["model-Q4", "model-Q4_2"])

    def test_split_models_count_once_with_total_size(self):
        with tempfile.TemporaryDirectory() as d:
            make(d, "big-Q2_0-00001-of-00002.gguf", 60)
            make(d, "big-Q2_0-00002-of-00002.gguf", 40)
            make(d, "mtp-big-Q8_0.gguf", 60)
            make(d, "rota-00001-of-00002.gguf", 60)  # falta el shard 2: todavia se esta bajando
            models = TM.scan_models([d])
        self.assertEqual([(m["id"], m["size"]) for m in models], [("big-Q2_0", 100 * MB)])

    def test_multiple_dirs_keys(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            make(a, "uno.gguf", 60)
            make(b, "sub/dos.gguf", 60)
            models = TM.scan_models([a, b])
        self.assertEqual([m["key"] for m in models], ["uno.gguf", str(Path(b, "sub/dos.gguf"))])


class Preset(unittest.TestCase):
    def setUp(self):
        self.models = [{"key": "x-MTP-Q4.gguf", "path": "/m/x-MTP-Q4.gguf", "id": "x-MTP-Q4"},
                       {"key": "y.gguf", "path": "/m/y.gguf", "id": "y"}]

    def test_defaults_detect_mtp_from_name(self):
        out = TM.build_preset(settings(), self.models)
        self.assertIn("[x-MTP-Q4]\nmodel = /m/x-MTP-Q4.gguf\nspec-type = draft-mtp\nspec-draft-n-max = 2\n", out)
        self.assertIn("[y]\nmodel = /m/y.gguf\n", out)
        self.assertIn("\n[*]\nn-gpu-layers = 99\n", out)
        self.assertIn(f"\nslot-save-path = {C.SLOTS_DIR}/\n", out)

    def test_extra_overrides_fields(self):
        s = settings(models={"y.gguf": {"ctx": "4096", "reasoning": "on", "extra": "ctx-size = 2048\nparallel = 1"}})
        out = TM.build_preset(s, self.models)
        self.assertIn("[y]\nmodel = /m/y.gguf\nreasoning = on\nctx-size = 2048\nparallel = 1\n", out)
        self.assertNotIn("ctx-size = 4096", out)

    def test_ai_mode_extra_only_in_ai_mode_and_wins(self):
        s = settings(models={"y.gguf": {"extra": "ctx-size = 8192\nparallel = 1", "extra_ai": "ctx-size = 32768"}})
        normal = TM.build_preset(s, self.models)
        ai = TM.build_preset(s, self.models, ai_mode=True)
        self.assertIn("[y]\nmodel = /m/y.gguf\nctx-size = 8192\nparallel = 1\n", normal)
        self.assertIn("[y]\nmodel = /m/y.gguf\nparallel = 1\nctx-size = 32768\n", ai)
        self.assertNotIn("ctx-size = 8192", ai)

    def test_ai_mode_fields_override_only_what_changes(self):
        s = settings(models={"y.gguf": {"mtp": True, "nmax": "2", "reasoning": "on", "ctx": "16384",
                                        "ai": {"ctx": "65536", "nmax": "3"}}})
        normal = TM.build_preset(s, self.models)
        ai = TM.build_preset(s, self.models, ai_mode=True)
        self.assertIn("[y]\nmodel = /m/y.gguf\nctx-size = 16384\nspec-type = draft-mtp\nspec-draft-n-max = 2\nreasoning = on\n", normal)
        self.assertIn("[y]\nmodel = /m/y.gguf\nctx-size = 65536\nspec-type = draft-mtp\nspec-draft-n-max = 3\nreasoning = on\n", ai)
        # MTP apagado solo en modo IA; las opciones crudas de modo IA siguen mandando sobre los campos
        s = settings(models={"y.gguf": {"mtp": True, "ai": {"mtp": False, "ctx": "65536"}, "extra_ai": "ctx-size = 32768"}})
        ai = TM.build_preset(s, self.models, ai_mode=True)
        self.assertNotIn("draft-mtp", ai.split("[y]")[1])
        self.assertIn("ctx-size = 32768", ai)
        self.assertNotIn("ctx-size = 65536", ai)


class Fields(unittest.TestCase):
    def test_controls_become_preset_lines(self):
        cfg = {"ngl": "all", "cpu_moe": "all", "parallel": "1", "kv": "q8_0", "fa": "on", "ubatch": "2048", "threads": "8"}
        self.assertEqual(TM.field_lines(cfg), ["n-gpu-layers = all", "cpu-moe = 1", "parallel = 1", "cache-type-k = q8_0",
                                               "cache-type-v = q8_0", "flash-attn = on", "ubatch-size = 2048", "threads = 8"])
        self.assertEqual(TM.field_lines({"cpu_moe": "20"}), ["n-cpu-moe = 20"])
        self.assertEqual(TM.field_lines({}), [])

    def test_extra_options_win_over_controls(self):
        s = settings(models={"y.gguf": {"parallel": "4", "kv": "q4_0", "extra": "parallel = 1"}})
        out = TM.build_preset(s, [{"key": "y.gguf", "path": "/m/y.gguf", "id": "y"}])
        self.assertIn("cache-type-k = q4_0", out)
        self.assertIn("parallel = 1", out)
        self.assertNotIn("parallel = 4", out)

    def test_invalid_values(self):
        errors, cfgs = TM.validate_settings("", {"a": {"ngl": "mucho", "kv": "q2", "parallel": "0", "threads": "8",
                                                       "ai": {"parallel": "x", "kv": "q8_0"}}})
        self.assertEqual(len(errors), 4)
        self.assertEqual((cfgs["a"]["threads"], cfgs["a"]["ai"]), ("8", {"kv": "q8_0"}))


class TextValidation(unittest.TestCase):
    def test_ok(self):
        errors, cfgs = TM.validate_settings("ctx-size = 8192", {"a": {"mtp": 1, "nmax": "3", "ctx": "4096"}})
        self.assertEqual(errors, [])
        expected = {f: "" for f in TM.FIELD_IDS}
        expected.update(mtp=True, nmax="3", ctx="4096", extra="", extra_ai="", ai={})
        self.assertEqual(cfgs["a"], expected)

    def test_ai_fields_keep_only_overrides(self):
        errors, cfgs = TM.validate_settings("", {"a": {"ai": {"mtp": False, "nmax": "", "reasoning": "off", "ctx": "65536"}}})
        self.assertEqual(errors, [])
        self.assertEqual(cfgs["a"]["ai"], {"mtp": False, "reasoning": "off", "ctx": "65536"})
        errors, _ = TM.validate_settings("", {"a": {"ai": {"nmax": "99", "ctx": "10", "reasoning": "x"}}})
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("(modo IA)" in e for e in errors))

    def test_errors(self):
        errors, _ = TM.validate_settings("mal", {"a": {"nmax": "99", "ctx": "10", "reasoning": "x", "extra": "?"}})
        self.assertEqual(len(errors), 5)


class ImgScan(unittest.TestCase):
    def test_companions_from_same_folder(self):
        with tempfile.TemporaryDirectory() as d:
            make(d, "sdxl.safetensors", 200)
            make(d, "flux/flux-Q4.gguf", 200)
            make(d, "flux/ae-vae.safetensors", 200)
            make(d, "flux/qwen3-4b.gguf", 200)
            make(d, "con espacio/x.gguf", 200)
            models = {m["id"]: m for m in IM.scan_img_models(d)}
        self.assertEqual(sorted(models), ["flux-Q4", "sdxl"])
        self.assertEqual([f for f, _ in models["flux-Q4"]["comps"]], ["sdllm", "sdvae"])
        self.assertEqual(models["sdxl"]["comps"], [])

    def test_kobold_args(self):
        m = {"path": "/i/f.gguf", "comps": [("sdvae", "/i/vae.st")]}
        args = IM.build_kobold_args(m, "--sdoffloadcpu --sdtiledvae 768")
        self.assertEqual(args[:4], ["--sdmodel", "/i/f.gguf", "--sdvae", "/i/vae.st"])
        self.assertEqual(args[-3:], ["--sdoffloadcpu", "--sdtiledvae", "768"])
        self.assertIn("--usevulkan", args)

    def test_kobold_backend_from_model_options(self):
        m = {"path": "/i/f.gguf", "comps": []}
        args = IM.build_kobold_args(m, "--usecuda")
        self.assertNotIn("--usevulkan", args)
        self.assertEqual(args[-1], "--usecuda")

    def test_validation_rejects_shell_chars(self):
        models = [{"key": "a"}]
        errors, cfgs = IM.validate_settings(models, {"a": {"extra": "--x; rm -rf ~"}, "zz": {"extra": ""}})
        self.assertTrue(errors)
        errors, cfgs = IM.validate_settings(models, {"a": {"extra": " --sdoffloadcpu "}, "zz": {"extra": ""}})
        self.assertEqual((errors, cfgs), ([], {"a": {"extra": "--sdoffloadcpu"}}))


if __name__ == "__main__":
    unittest.main()


class GlobalFields(unittest.TestCase):
    def test_controls_go_to_the_global_section_and_text_wins(self):
        s = settings(global_fields={"ctx": "8192", "parallel": "1", "ngl": "50"})
        s["global"] = "n-gpu-layers = 99\nreasoning-format = deepseek"
        out = TM.build_preset(s, [])
        top = out.split("[*]\n", 1)[1]
        self.assertIn("ctx-size = 8192\nparallel = 1\nn-gpu-layers = 99\nreasoning-format = deepseek\n", top)
        self.assertNotIn("n-gpu-layers = 50", out)

    def test_validation_skips_empty_and_model_only_fields(self):
        errors, out = TM.validate_global_fields({"ctx": "16384", "kv": "", "mtp": True, "parallel": "x"})
        self.assertEqual(out, {"ctx": "16384"})
        self.assertEqual(len(errors), 1)

    def test_new_install_gets_defaults_but_old_settings_do_not(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "settings.json"
            with mock.patch.object(C, "SETTINGS_FILE", f):
                self.assertEqual(C.load_settings()["global_fields"], C.DEFAULT_GLOBAL_FIELDS)
                f.write_text(json.dumps({"global": "n-gpu-layers = 99"}))
                self.assertEqual(C.load_settings()["global_fields"], {})
