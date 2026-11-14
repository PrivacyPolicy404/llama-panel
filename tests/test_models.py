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
        self.assertTrue(out.split("\n")[2:4] == ["[*]", "n-gpu-layers = 99"])

    def test_extra_overrides_fields(self):
        s = settings(models={"y.gguf": {"ctx": "4096", "reasoning": "on", "extra": "ctx-size = 2048\nparallel = 1"}})
        out = TM.build_preset(s, self.models)
        self.assertIn("[y]\nmodel = /m/y.gguf\nreasoning = on\nctx-size = 2048\nparallel = 1\n", out)
        self.assertNotIn("ctx-size = 4096", out)


class TextValidation(unittest.TestCase):
    def test_ok(self):
        errors, cfgs = TM.validate_settings("ctx-size = 8192", {"a": {"mtp": 1, "nmax": "3", "ctx": "4096"}})
        self.assertEqual(errors, [])
        self.assertEqual(cfgs["a"], {"mtp": True, "nmax": "3", "reasoning": "", "ctx": "4096", "extra": ""})

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

    def test_validation_rejects_shell_chars(self):
        models = [{"key": "a"}]
        errors, cfgs = IM.validate_settings(models, {"a": {"extra": "--x; rm -rf ~"}, "zz": {"extra": ""}})
        self.assertTrue(errors)
        errors, cfgs = IM.validate_settings(models, {"a": {"extra": " --sdoffloadcpu "}, "zz": {"extra": ""}})
        self.assertEqual((errors, cfgs), ([], {"a": {"extra": "--sdoffloadcpu"}}))


if __name__ == "__main__":
    unittest.main()
