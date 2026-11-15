"""Tamano de los expertos MoE por capa en un GGUF, leyendo solo la cabecera (sin numpy).

Uso: python3 -I gguf_sizes.py MODELO.gguf   (para modelos partidos, pasar el shard que tiene los pesos)
Sirve para calcular cuantas capas de expertos entran en un limite de memoria fija (GTT) con -ot.
"""
import collections, os, re, struct, sys

path = sys.argv[1]
f = open(path, "rb")
rd = lambda fmt: struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))
assert f.read(4) == b"GGUF"
version, n_tensors, n_kv = rd("I")[0], rd("Q")[0], rd("Q")[0]
SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
alignment = 32


def rstr():
    n = rd("Q")[0]
    return f.read(n).decode("utf-8", "replace")


def rval(t):
    if t == 8:
        return rstr()
    if t == 9:
        it, n = rd("I")[0], rd("Q")[0]
        return [rval(it) for _ in range(n)]
    fmt = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}[t]
    return rd(fmt)[0]


for _ in range(n_kv):
    k = rstr(); t = rd("I")[0]; v = rval(t)
    if k == "general.alignment":
        alignment = v
infos = []
for _ in range(n_tensors):
    name = rstr(); nd = rd("I")[0]; rd("Q" * nd); rd("I"); off = rd("Q")[0]
    infos.append((off, name))
data_start = (f.tell() + alignment - 1) // alignment * alignment
end = os.path.getsize(path) - data_start
infos.sort()
per, other = collections.defaultdict(int), 0
for i, (off, name) in enumerate(infos):
    size = (infos[i + 1][0] if i + 1 < len(infos) else end) - off
    m = re.match(r"blk\.(\d+)\.ffn_.*_exps", name)
    if m:
        per[int(m[1])] += size
    else:
        other += size
tot = sum(per.values())
print(f"expertos: {tot/2**30:.1f} GiB en {len(per)} capas ({tot/len(per)/2**30:.2f} GiB por capa); resto: {other/2**30:.1f} GiB")
acc = 0
for n in range(1, len(per) + 1):
    acc += per[n - 1]
    if n % 4 == 0 and n >= 24:
        print(f"  capas 0-{n-1}: {acc/2**30:.1f} GiB")
