import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "archivo"))
import archivar as A  # noqa: E402


class ManifiestoTest(unittest.TestCase):
    def test_el_manifiesto_real_se_lee(self):
        etapas = A.leer_manifiesto(A.MANIFIESTO.read_text())
        self.assertEqual([s.num for s in etapas], list(range(1, len(etapas) + 1)))
        self.assertTrue(all(s.entradas for s in etapas))
        # la etapa 1 no necesita internet
        self.assertTrue(all(e.tipo == "local" for e in etapas[0].entradas))

    def test_errores(self):
        for texto in ("[2] empieza en 2", "hf:a/b | * | x | ok | sin etapa",
                      "[1] t\nhf:a/b | * | x | quizas | nota", "[1] t\nftp:a | * | x | ok | n",
                      "[1] t\nhf:a/b | * | x | ok | n\nhf:c/d | * | x | ok | carpeta repetida",
                      "[1] t\nhf:a/b | * | ../afuera | ok | n", "[1] t\nhf:a/b | * | x | ok"):
            with self.assertRaises(A.ErrorArchivo, msg=texto):
                A.leer_manifiesto(texto)

    def test_patrones_y_exclusiones(self):
        e = A.leer_manifiesto("[1] t\nhf:a/b | *Q6_K*, !*Q6_K_L* | x | ok | n")[0].entradas[0]
        self.assertEqual((e.incluir, e.excluir), (["*Q6_K*"], ["*Q6_K_L*"]))
        self.assertTrue(A.coincide("m-Q6_K.gguf", e.incluir, e.excluir))
        self.assertTrue(A.coincide("Q6_K/m-Q6_K-00001-of-00002.gguf", e.incluir, e.excluir))
        self.assertFalse(A.coincide("m-Q6_K_L.gguf", e.incluir, e.excluir))
        # README y LICENSE de la raíz siempre; los de subcarpetas no
        self.assertTrue(A.coincide("LICENSE.txt", e.incluir, e.excluir, A.EXTRAS_HF))
        self.assertFalse(A.coincide("sub/README.md", e.incluir, e.excluir, A.EXTRAS_HF))


class TransferirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.datos = os.urandom(3 * 1024 * 1024 + 17)
        self.src = self.tmp / "src.bin"
        self.src.write_bytes(self.datos)
        self.sha = hashlib.sha256(self.datos).hexdigest()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def test_retoma_un_part(self):
        dst = self.tmp / "out" / "m.gguf"
        dst.parent.mkdir()
        (dst.parent / "m.gguf.part").write_bytes(self.datos[:1_000_000])
        desdes = []

        def abrir(desde):
            desdes.append(desde)
            return A.abrir_local(self.src)(desde)
        self.assertEqual(A.transferir(abrir, dst, len(self.datos), self.sha), self.sha)
        self.assertEqual(desdes, [1_000_000])
        self.assertEqual(dst.read_bytes(), self.datos)
        self.assertFalse((dst.parent / "m.gguf.part").exists())

    def test_corte_y_reintento(self):
        dst = self.tmp / "m.gguf"
        datos = self.datos

        class Cortado(io.BytesIO):
            def read(self, n=-1):
                b = super().read(min(n, 500_000))
                if self.tell() > 1_200_000:
                    raise ConnectionResetError("corte")
                return b
        llamadas = []

        def abrir(desde):
            llamadas.append(desde)
            if len(llamadas) == 1:
                return Cortado(datos), True
            return io.BytesIO(datos[desde:]), True
        with mock.patch.object(A.time, "sleep"):
            self.assertEqual(A.transferir(abrir, dst, len(datos), self.sha), self.sha)
        self.assertEqual(llamadas, [0, 1_000_000])
        self.assertEqual(dst.read_bytes(), datos)

    def test_fuente_que_ignora_el_rango_vuelve_a_empezar(self):
        dst = self.tmp / "m.gguf"
        (self.tmp / "m.gguf.part").write_bytes(b"basura" * 100)
        r = A.transferir(lambda desde: (io.BytesIO(self.datos), False), dst, len(self.datos), self.sha)
        self.assertEqual(r, self.sha)

    def test_sha_distinto_borra(self):
        dst = self.tmp / "m.gguf"
        with self.assertRaises(A.ErrorArchivo):
            A.transferir(A.abrir_local(self.src), dst, len(self.datos), "0" * 64)
        self.assertFalse(dst.exists())
        self.assertFalse((self.tmp / "m.gguf.part").exists())

    def test_sin_lugar(self):
        uso = mock.Mock(free=10)
        with mock.patch.object(A.shutil, "disk_usage", return_value=uso), self.assertRaises(A.ErrorArchivo):
            A.transferir(A.abrir_local(self.src), self.tmp / "m.gguf", len(self.datos))

    def test_oid_git_como_git(self):
        try:
            esperado = subprocess.run(["git", "hash-object", str(self.src)], capture_output=True,
                                      text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("sin git")
        self.assertEqual(A.oid_git(self.src), esperado)


class LocalTest(unittest.TestCase):
    def test_enlace_duro_se_guarda_una_vez_y_se_retoma(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            a, b, dest = t / "a", t / "b", t / "archivo"
            a.mkdir(), b.mkdir(), dest.mkdir()
            (a / "shard1.gguf").write_bytes(b"1" * 1000)
            (a / "ngramas.gguf").write_bytes(b"2" * 5000)
            os.link(a / "ngramas.gguf", b / "ngramas.gguf")
            (b / "shard1.gguf").write_bytes(b"3" * 1000)
            etapas = A.leer_manifiesto(f"[1] t\nlocal:{a} | * | m/a | ok | n\nlocal:{b} | *.gguf | m/b | ok | n")
            enlaces = {}
            for e in etapas[0].entradas:
                carpeta = dest / e.carpeta
                carpeta.mkdir(parents=True)
                A.bajar_local(e, carpeta, A.leer_estado(carpeta), enlaces)
            self.assertEqual((dest / "m/a/ngramas.gguf").stat().st_ino, (dest / "m/b/ngramas.gguf").stat().st_ino)
            sums = (dest / "m/b/SHA256SUMS").read_text()
            self.assertIn(hashlib.sha256(b"2" * 5000).hexdigest() + "  ngramas.gguf", sums)
            self.assertIn("Fuente: local:", (dest / "m/b/ARCHIVO.md").read_text())
            # segunda corrida: no copia nada
            with mock.patch.object(A, "transferir", side_effect=AssertionError("copió de nuevo")):
                for e in etapas[0].entradas:
                    A.bajar_local(e, dest / e.carpeta, A.leer_estado(dest / e.carpeta), {})


if __name__ == "__main__":
    unittest.main()
