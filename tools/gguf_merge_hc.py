"""Une hc_{attn,ffn}_down + hc_{attn,ffn}_inject en el tensor hc_*_down_inject que entiende llama.cpp (qwen4exp).

Uso: python3 -I gguf_merge_hc.py ENTRADA-00001-of-0000N.gguf SALIDA-00001-of-0000N.gguf

Por que: hc_inject es una multiplicacion de 4 filas x 10240 (BF16). La GPU la lanza con 2 grupos de trabajo para 36 CUs
y tarda 14.5 us (5.6 GB/s); 96 por token = 1.4 ms, mas sus 96 escalas. Con el tensor unido llama.cpp hace una sola
multiplicacion de 324 filas y una sola escala (src/models/qwen4exp.cpp, build_hc_mix: "one matmul for both").

- hc_*_down tiene que estar en Q8_0 (el modelo -hcq8); hc_*_inject (BF16/F16/F32) se cuantiza a Q8_0 aca, igual que
  quantize_row_q8_0_ref de ggml. Orden de filas: primero las de down, despues las de inject.
- Solo reescribe el shard que tiene esos tensores. Los demas shards se usan tal cual (hardlink); se ajusta
  split.tensors.count en este shard. Sin numpy.
"""
import math, os, re, struct, sys

QK8_0, Q8_0, BF16, F16, F32 = 32, 8, 30, 1, 0
TYPE_SIZE = {Q8_0: (34, 32), BF16: (2, 1), F16: (2, 1), F32: (4, 1)}
SZ = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

src, dst = sys.argv[1], sys.argv[2]
f = open(src, "rb")
rd = lambda fmt: struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))


def rstr():
    n = rd("Q")[0]
    return f.read(n)


def skip_val(t):
    if t == 8:
        rstr()
    elif t == 9:
        at, n = rd("I")[0], rd("Q")[0]
        if at == 8:
            for _ in range(n):
                rstr()
        else:
            f.read(SZ[at] * n)
    else:
        f.read(SZ[t])


assert f.read(4) == b"GGUF"
version, n_tensors, n_kv = rd("I")[0], rd("Q")[0], rd("Q")[0]
kvs = []  # (key, raw bytes of key+type+value)
alignment = 32
for _ in range(n_kv):
    start = f.tell()
    key = rstr().decode()
    t = rd("I")[0]
    vstart = f.tell()
    skip_val(t)
    end = f.tell()
    if key == "general.alignment":
        f.seek(vstart); alignment = rd("I")[0]; f.seek(end)
    f.seek(start)
    kvs.append([key, t, f.read(end - start)])

tensors = []  # name, dims, type, offset
for _ in range(n_tensors):
    name = rstr().decode()
    nd = rd("I")[0]
    dims = list(rd("Q" * nd))
    t, off = rd("I")[0], rd("Q")[0]
    tensors.append([name, dims, t, off])
pad = lambda x: (x + alignment - 1) // alignment * alignment
data_start = pad(f.tell())


def nbytes(dims, t):
    bs, be = TYPE_SIZE.get(t, (None, None))
    n = math.prod(dims)
    return n // be * bs


def read_tensor(tn):
    f.seek(data_start + tn[3])
    return f.read(nbytes(tn[1], tn[2]))


def to_floats(raw, t):
    if t == F32:
        return struct.unpack(f"<{len(raw) // 4}f", raw)
    if t == F16:
        return struct.unpack(f"<{len(raw) // 2}e", raw)
    if t == BF16:
        u = struct.unpack(f"<{len(raw) // 2}H", raw)
        return struct.unpack(f"<{len(u)}f", struct.pack(f"<{len(u)}I", *[x << 16 for x in u]))
    raise SystemExit(f"tipo {t} no soportado para inject")


def quant_q8_0(xs):
    out = bytearray()
    for i in range(0, len(xs), QK8_0):
        blk = xs[i:i + QK8_0]
        amax = max(abs(v) for v in blk)
        d = amax / 127.0
        idv = 1.0 / d if d else 0.0
        q = []
        for v in blk:
            r = v * idv
            r = math.floor(r + 0.5) if r >= 0 else -math.floor(-r + 0.5)  # roundf: medio lejos del cero
            q.append(max(-128, min(127, int(r))))
        out += struct.pack("<e", d) + struct.pack(f"<{QK8_0}b", *q)
    return bytes(out)


by_name = {t[0]: t for t in tensors}
ordered = sorted(tensors, key=lambda t: t[3])
file_end = os.path.getsize(src) - data_start
span = {t[0]: (ordered[i + 1][3] if i + 1 < len(ordered) else file_end) - t[3] for i, t in enumerate(ordered)}
merged = {}  # nombre del tensor down -> (nombre nuevo, dims, bytes)
drop = set()
for t in tensors:
    m = re.fullmatch(r"blk\.(\d+)\.hc_(attn|ffn)_down\.weight", t[0])
    if not m:
        continue
    inj = by_name.get(f"blk.{m[1]}.hc_{m[2]}_inject.weight")
    if inj is None:
        continue
    if t[2] != Q8_0:
        raise SystemExit(f"{t[0]} no es Q8_0 (usar el modelo -hcq8)")
    assert inj[1][0] == t[1][0] and len(t[1]) == len(inj[1]) == 2
    inj_q = quant_q8_0(to_floats(read_tensor(inj), inj[2])) if inj[2] != Q8_0 else read_tensor(inj)
    data = read_tensor(t) + inj_q
    merged[t[0]] = (f"blk.{m[1]}.hc_{m[2]}_down_inject.weight", [t[1][0], t[1][1] + inj[1][1]], data)
    drop.add(inj[0])
if not merged:
    raise SystemExit("no hay pares hc_*_down / hc_*_inject en este archivo")

# nueva lista de tensores y offsets (mismo orden; el unido ocupa el lugar de down)
new = []
off = 0
for t in tensors:
    if t[0] in drop:
        continue
    if t[0] in merged:
        name, dims, data = merged[t[0]]
        new.append((name, dims, Q8_0, off, ("mem", data)))
        size = len(data)
    else:
        size = span[t[0]]  # incluye el relleno de alineacion: no hace falta conocer el tamano de cada tipo
        new.append((t[0], t[1], t[2], off, ("file", t[3], size)))
    off = pad(off + size)

removed = len(tensors) - len(new)
for kv in kvs:
    if kv[0] == "split.tensors.count":
        kb = kv[0].encode()
        head = struct.pack("<Q", len(kb)) + kb + struct.pack("<I", kv[1])
        fmt = {4: "I", 5: "i", 2: "H", 3: "h", 10: "Q", 11: "q"}[kv[1]]
        old = struct.unpack("<" + fmt, kv[2][len(head):])[0]
        kv[2] = head + struct.pack("<" + fmt, old - removed)
        print(f"split.tensors.count {old} -> {old - removed}")

tmp = dst + ".part"
with open(tmp, "wb") as o:
    o.write(b"GGUF" + struct.pack("<IQQ", version, len(new), len(kvs)))
    for kv in kvs:
        o.write(kv[2])
    for name, dims, t, toff, _ in new:
        nb = name.encode()
        o.write(struct.pack("<Q", len(nb)) + nb + struct.pack("<I", len(dims)) + struct.pack(f"<{len(dims)}Q", *dims))
        o.write(struct.pack("<IQ", t, toff))
    o.write(b"\0" * (pad(o.tell()) - o.tell()))
    base = o.tell()
    for name, dims, t, toff, srcinfo in new:
        o.write(b"\0" * (base + toff - o.tell()))
        if srcinfo[0] == "mem":
            o.write(srcinfo[1])
        else:
            f.seek(data_start + srcinfo[1])
            left = srcinfo[2]
            while left:
                chunk = f.read(min(left, 64 << 20))
                o.write(chunk)
                left -= len(chunk)
os.replace(tmp, dst)
print(f"{len(merged)} tensores unidos, {removed} menos en total -> {dst}")
