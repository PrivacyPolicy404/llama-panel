#!/usr/bin/env bash
# Actualiza llama.cpp manteniendo el parche local (rama ot-host-buffers de tu fork).
#
#   scripts/update-llama.sh --check   # solo dice cuantos commits nuevos hay en el oficial
#   scripts/update-llama.sh           # rebase sobre el oficial, compila, verifica, sube al fork y reinicia el router
#
# Remotos esperados en ~/llama.cpp: origin = tu fork, upstream = ggml-org/llama.cpp (sin push).
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/llama.cpp}"
BRANCH="${LLAMA_BRANCH:-ot-host-buffers}"
JOBS="${JOBS:-14}"
cd "$LLAMA_DIR"

say() { printf '\n== %s\n' "$*"; }

[ "$(git branch --show-current)" = "$BRANCH" ] || { echo "Error: ~/llama.cpp no esta en la rama $BRANCH"; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "Error: hay cambios sin commitear en $LLAMA_DIR"; exit 1; }

say "Buscando novedades en ggml-org/llama.cpp"
git fetch --quiet upstream master
behind=$(git rev-list --count HEAD..upstream/master)
echo "Commits nuevos en el oficial: $behind (base actual: $(git merge-base HEAD upstream/master | cut -c1-7))"
if [ "${1:-}" = "--check" ] || [ "$behind" = 0 ]; then
    [ "$behind" = 0 ] && echo "Ya estas al dia."
    exit 0
fi

old_head=$(git rev-parse HEAD)
say "Reaplicando el parche sobre upstream/master"
if ! git rebase --quiet upstream/master; then
    git rebase --abort
    echo "Error: el parche ya no aplica limpio sobre la version nueva. No se cambio nada."
    echo "Hay que adaptarlo a mano (ver ~/llama-panel/patches/README.md)."
    exit 1
fi

say "Compilando"
if ! cmake --build build --target llama-server llama-bench -j "$JOBS" > /tmp/llama-build.log 2>&1; then
    tail -20 /tmp/llama-build.log
    echo "Error de compilacion. Volviendo a la version anterior ($old_head)."
    git reset --quiet --hard "$old_head"
    cmake --build build --target llama-server llama-bench -j "$JOBS" > /tmp/llama-build.log 2>&1
    exit 1
fi

say "Verificando que el parche siga activo"
grep -q "ggml_backend_dev_host_buffer_type(dev)" common/arg.cpp || { echo "Error: el parche no esta en common/arg.cpp"; exit 1; }
./build/bin/llama-server --version 2>&1 | head -1

say "Subiendo la rama a tu fork"
git push --quiet --force-with-lease origin "$BRANCH"

if [ "$(systemctl --user is-active llama-router || true)" = active ]; then
    say "Reiniciando el servidor de texto"
    systemctl --user restart llama-router
fi
say "Listo: llama.cpp actualizado ($behind commits nuevos) con el parche aplicado."
echo "Verificar: python3 ~/llama-panel/tools/bench/smoke_models.py"
echo "Para volver a la version anterior si algo anda peor:"
echo "  git -C $LLAMA_DIR reset --hard $old_head && cmake --build $LLAMA_DIR/build --target llama-server llama-bench -j $JOBS"
echo "  git -C $LLAMA_DIR push --force-with-lease origin $BRANCH"
