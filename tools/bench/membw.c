// Ancho de banda de lectura de la RAM. Uso: gcc -O3 -march=native -fopenmp membw.c -o membw && ./membw
#include <immintrin.h>
#include <omp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
// lectura secuencial con AVX2 de un buffer de 4 GiB, con N hilos
int main(int argc, char **argv) {
    size_t n = (size_t)4 << 30;
    char *buf = aligned_alloc(64, n);
    memset(buf, 1, n);
    for (int t = 1; t <= 16; t *= 2) {
        omp_set_num_threads(t == 16 ? 16 : t);
        double best = 0;
        for (int rep = 0; rep < 3; rep++) {
            double t0 = omp_get_wtime();
            __m256i acc = _mm256_setzero_si256();
            #pragma omp parallel
            {
                __m256i a = _mm256_setzero_si256();
                #pragma omp for schedule(static)
                for (size_t i = 0; i < n; i += 128) {
                    a = _mm256_add_epi8(a, _mm256_load_si256((__m256i *)(buf + i)));
                    a = _mm256_add_epi8(a, _mm256_load_si256((__m256i *)(buf + i + 32)));
                    a = _mm256_add_epi8(a, _mm256_load_si256((__m256i *)(buf + i + 64)));
                    a = _mm256_add_epi8(a, _mm256_load_si256((__m256i *)(buf + i + 96)));
                }
                #pragma omp critical
                acc = _mm256_add_epi8(acc, a);
            }
            double dt = omp_get_wtime() - t0;
            if (n / dt / 1e9 > best) best = n / dt / 1e9;
            volatile int sink = _mm256_extract_epi32(acc, 0); (void)sink;
        }
        printf("%2d hilos: %.1f GB/s\n", t, best);
    }
    return 0;
}
