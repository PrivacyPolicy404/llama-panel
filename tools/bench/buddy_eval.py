"""Mide el comportamiento del compañero contra el modelo de texto cargado (casos en llama_panel/buddy_eval.py).

Uso:
  python3 tools/bench/buddy_eval.py                    # configuración de fábrica, 3 repeticiones
  python3 tools/bench/buddy_eval.py --user-config      # las secciones guardadas del usuario
  python3 tools/bench/buddy_eval.py --reps 5 --label secciones --only maquina,charla
  python3 tools/bench/buddy_eval.py --quick            # 10 casos, 1 repetición (lo mismo que el botón «Probar»)

Usa el único slot del servidor: pisa el caché de la charla real (la próxima tarda ~20 s, o se precalienta).
Resultados en ~/llama-panel-results/<fecha>-buddy-eval/<label>.json.
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from llama_panel import buddy  # noqa: E402
from llama_panel import buddy_eval as E  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--label", default="actual")
    ap.add_argument("--user-config", action="store_true", help="usar la configuración guardada del usuario")
    ap.add_argument("--only", default="", help="categorías separadas por coma")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--hists", default="limpio,contaminado",
                    help="limpio, contaminado, contaminado-sin-herramientas (separados por coma)")
    ap.add_argument("--temperature", type=float, default=None, help="por defecto, la del servidor")
    ap.add_argument("--out", default=str(Path.home() / "llama-panel-results" / f"{date.today()}-buddy-eval"))
    args = ap.parse_args()
    cfg = buddy.load_config() if args.user_config else buddy.factory_config()

    def progress(done, total, r):
        if not r["ok"]:
            print(f"  ✗ [{r['hist']}] {r['msg']} → {r['calls'] or r['text'][:80]!r} ({r['expect']})", flush=True)

    t0 = time.time()
    out = E.run(cfg, quick=args.quick, reps=args.reps, only=[x for x in args.only.split(",") if x],
                progress=progress, hists=args.hists.split(","), temperature=args.temperature)
    print(f"\n{args.label} ({out['model']}): {time.time() - t0:.0f} s")
    for key, (ok, n) in out["summary"].items():
        print(f"  {key:22} {ok:3}/{n:<3} {100 * ok / n:5.1f} %")
    lat = sorted(r["s"] for r in out["results"])
    print(f"  primera ronda: mediana {lat[len(lat) // 2]:.1f} s")
    path = Path(args.out)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{args.label}.json").write_text(json.dumps({"label": args.label, "reps": args.reps, **out},
                                                       ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
