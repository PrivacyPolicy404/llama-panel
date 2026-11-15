// Uso: gcc -O3 -march=native -fopenmp q2bench.c -o q2bench -lm && ./q2bench   (variables: PF=bytes de prefetch, HUGE=1)
// Microbenchmark: kernel AVX2 Q2_0 x Q8_0 actual (copiado de llama.cpp) vs uno con activaciones reacomodadas.
// gcc -O3 -march=native -fopenmp q2bench.c -o q2bench
#include <immintrin.h>
#include <omp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/mman.h>
static int PF = 0;

typedef uint16_t ggml_half;
typedef struct { ggml_half d; uint8_t qs[16]; } block_q2_0;  // 64 pesos
typedef struct { ggml_half d; int8_t qs[32]; } block_q8_0;   // 32 valores
static inline float h2f(ggml_half h) { return _cvtsh_ss(h); }
static inline ggml_half f2h(float f) { return _cvtss_sh(f, 0); }

static inline float hsum_float_8(const __m256 x) {
    __m128 res = _mm256_extractf128_ps(x, 1);
    res = _mm_add_ps(res, _mm256_castps256_ps128(x));
    res = _mm_add_ps(res, _mm_movehl_ps(res, res));
    res = _mm_add_ss(res, _mm_movehdup_ps(res));
    return _mm_cvtss_f32(res);
}

// ---------- referencia escalar ----------
static float dot_ref(int n, const block_q2_0 *x, const block_q8_0 *y) {
    double s = 0;
    for (int i = 0; i < n / 64; i++) {
        for (int k = 0; k < 2; k++) {
            int si = 0;
            for (int j = 0; j < 32; j++) {
                int w = (x[i].qs[k * 8 + j / 4] >> (2 * (j % 4))) & 3;
                si += (w - 1) * y[2 * i + k].qs[j];
            }
            s += (double)h2f(x[i].d) * h2f(y[2 * i + k].d) * si;
        }
    }
    return (float)s;
}

// ---------- kernel actual de llama.cpp ----------
static inline __m256i q2_0_unpack_32_avx2(const uint8_t * qs) {
    __m256i v = _mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i *) qs));
    v = _mm256_or_si256(v, _mm256_slli_epi32(v, 12));
    v = _mm256_or_si256(v, _mm256_slli_epi32(v,  6));
    return _mm256_and_si256(v, _mm256_set1_epi32(0x03030303));
}
static inline __m256 q2_0_dot_32_avx2(const uint8_t * qs, const int8_t * qy) {
    const __m256i v = q2_0_unpack_32_avx2(qs);
    const __m256i y = _mm256_loadu_si256((const __m256i *) qy);
    const __m256i p = _mm256_sub_epi16(_mm256_maddubs_epi16(v, y), _mm256_maddubs_epi16(_mm256_set1_epi8(1), y));
    return _mm256_cvtepi32_ps(_mm256_madd_epi16(p, _mm256_set1_epi16(1)));
}
static inline __m256 q2_0_acc_block_avx2(const block_q2_0 * x, const block_q8_0 * y, __m256 acc) {
    const float  dx = h2f(x->d);
    const __m256 d0 = _mm256_set1_ps(dx * h2f(y[0].d));
    const __m256 d1 = _mm256_set1_ps(dx * h2f(y[1].d));
    const __m256 p0 = q2_0_dot_32_avx2(x->qs + 0, y[0].qs);
    const __m256 p1 = q2_0_dot_32_avx2(x->qs + 8, y[1].qs);
    acc = _mm256_fmadd_ps(d0, p0, acc);
    return _mm256_fmadd_ps(d1, p1, acc);
}
__attribute__((noinline)) static void dot_old(int n, float *s, const block_q2_0 *x, const block_q8_0 *y) {
    const int nb = n / 64;
    __m256 acc0 = _mm256_setzero_ps(), acc1 = _mm256_setzero_ps();
    int i = 0;
    for (; i + 1 < nb; i += 2) {
        acc0 = q2_0_acc_block_avx2(x + i + 0, y + 2*i + 0, acc0);
        acc1 = q2_0_acc_block_avx2(x + i + 1, y + 2*i + 2, acc1);
    }
    if (i < nb) acc0 = q2_0_acc_block_avx2(x + i, y + 2*i, acc0);
    *s = hsum_float_8(_mm256_add_ps(acc0, acc1));
}

// ---------- kernel nuevo ----------
// Activaciones reacomodadas por bloque Q2_0 (64 pesos):
//   yA = [plano0 | plano1], yB = [plano2 | plano3] donde plano k byte b = y[4b + k]
//   (los pesos del byte b de qs son 4b..4b+3: el plano k sale de (qs >> 2k) & 3)
//   sy  = suma de y por carril de 32 bits de (A + B)  -> correccion del -1
//   dy  = escala de y por carril (bytes 0-7 de cada mitad = bloque Q8 0, 8-15 = bloque 1)
typedef struct { __m256i yA, yB, sy; __m256 dy; } q2y_t;

static void prep_y(int n, const block_q8_0 *y, q2y_t *out) {
    for (int i = 0; i < n / 64; i++) {
        int8_t a[32], b[32];
        for (int k = 0; k < 4; k++)
            for (int bb = 0; bb < 16; bb++) {
                int j = 4 * bb + k;  // peso dentro del bloque de 64
                int8_t v = y[2 * i + j / 32].qs[j % 32];
                if (k < 2) a[k * 16 + bb] = v; else b[(k - 2) * 16 + bb] = v;
            }
        out[i].yA = _mm256_loadu_si256((const __m256i *)a);
        out[i].yB = _mm256_loadu_si256((const __m256i *)b);
        const __m256i one = _mm256_set1_epi8(1);
        out[i].sy = _mm256_madd_epi16(_mm256_add_epi16(_mm256_maddubs_epi16(one, out[i].yA), _mm256_maddubs_epi16(one, out[i].yB)), _mm256_set1_epi16(1));
        const float d0 = h2f(y[2 * i].d), d1 = h2f(y[2 * i + 1].d);
        out[i].dy = _mm256_setr_ps(d0, d0, d1, d1, d0, d0, d1, d1);
    }
}

static inline __m256 q2_new_block(const block_q2_0 *x, const q2y_t *py, __m256 acc) {
    const __m256i q  = _mm256_broadcastsi128_si256(_mm_loadu_si128((const __m128i *)x->qs));
    const __m256i m3 = _mm256_set1_epi8(3);
    const __m256i A  = _mm256_and_si256(_mm256_srlv_epi64(q, _mm256_setr_epi64x(0, 0, 2, 2)), m3);
    const __m256i B  = _mm256_and_si256(_mm256_srlv_epi64(q, _mm256_setr_epi64x(4, 4, 6, 6)), m3);
    const __m256i p  = _mm256_add_epi16(_mm256_maddubs_epi16(A, py->yA), _mm256_maddubs_epi16(B, py->yB));
    const __m256i p32 = _mm256_sub_epi32(_mm256_madd_epi16(p, _mm256_set1_epi16(1)), py->sy);
    const __m256 f = _mm256_mul_ps(_mm256_cvtepi32_ps(p32), py->dy);
    return _mm256_fmadd_ps(_mm256_set1_ps(h2f(x->d)), f, acc);
}
__attribute__((noinline)) static void dot_new(int n, float *s, const block_q2_0 *x, const q2y_t *py) {
    const int nb = n / 64;
    __m256 acc0 = _mm256_setzero_ps(), acc1 = _mm256_setzero_ps();
    int i = 0;
    for (; i + 1 < nb; i += 2) {
        if (PF) _mm_prefetch((const char *)(x + i) + PF, _MM_HINT_T0);
        acc0 = q2_new_block(x + i, py + i, acc0);
        acc1 = q2_new_block(x + i + 1, py + i + 1, acc1);
    }
    if (i < nb) acc0 = q2_new_block(x + i, py + i, acc0);
    *s = hsum_float_8(_mm256_add_ps(acc0, acc1));
}
// 4 filas por llamada (comparten cada carga de y)
__attribute__((noinline)) static void dot_new4(int n, float *s, const block_q2_0 *x, size_t bx, const q2y_t *py) {
    const int nb = n / 64;
    const block_q2_0 *x0 = x, *x1 = (const block_q2_0 *)((const char *)x + bx), *x2 = (const block_q2_0 *)((const char *)x + 2*bx), *x3 = (const block_q2_0 *)((const char *)x + 3*bx);
    __m256 a0 = _mm256_setzero_ps(), a1 = a0, a2 = a0, a3 = a0;
    for (int i = 0; i < nb; i++) {
        a0 = q2_new_block(x0 + i, py + i, a0);
        a1 = q2_new_block(x1 + i, py + i, a1);
        a2 = q2_new_block(x2 + i, py + i, a2);
        a3 = q2_new_block(x3 + i, py + i, a3);
    }
    s[0] = hsum_float_8(a0); s[1] = hsum_float_8(a1); s[2] = hsum_float_8(a2); s[3] = hsum_float_8(a3);
}

static void fill(block_q2_0 *x, size_t nbx, block_q8_0 *y, size_t nby) {
    uint32_t r = 12345;
    #define RND() (r = r * 1664525u + 1013904223u)
    for (size_t i = 0; i < nbx; i++) { x[i].d = f2h(0.001f + (RND() % 1000) * 1e-5f); for (int j = 0; j < 16; j++) x[i].qs[j] = RND() >> 24; }
    for (size_t i = 0; i < nby; i++) { y[i].d = f2h(0.01f + (RND() % 1000) * 1e-4f); for (int j = 0; j < 32; j++) y[i].qs[j] = (int8_t)((RND() >> 24) - 128); }
}

// recorre `rows` filas de k pesos, como un experto (gate/up: 640x2560, down: 2560x640)
static double run(int mode, int k, int rows, size_t nmat, block_q2_0 *X, const block_q8_0 *Y, int threads, int reps, int same) {
    const int nb = k / 64;
    double best = 1e9;
    for (int rep = 0; rep < reps; rep++) {
        double t0 = omp_get_wtime();
        #pragma omp parallel num_threads(threads)
        {
            q2y_t py[64];
            float out[4];
            volatile float sink = 0;
            #pragma omp for schedule(static)
            for (size_t m = 0; m < nmat; m++) {
                const block_q2_0 *xm = X + (same ? 0 : m) * (size_t)rows * nb;
                if (mode >= 1) prep_y(k, Y, py);
                for (int r = 0; r < rows; r += (mode == 2 ? 4 : 1)) {
                    if (mode == 0) dot_old(k, out, xm + (size_t)r * nb, Y);
                    else if (mode == 1) dot_new(k, out, xm + (size_t)r * nb, py);
                    else dot_new4(k, out, xm + (size_t)r * nb, nb * sizeof(block_q2_0), py);
                    sink += out[0];
                }
            }
        }
        double dt = omp_get_wtime() - t0;
        if (dt < best) best = dt;
    }
    return (double)nmat * rows * nb * sizeof(block_q2_0) / best / 1e9;  // GB/s de pesos
}

int main(void) {
    if (getenv("PF")) PF = atoi(getenv("PF"));
    // correccion
    for (int k = 640; k <= 2560; k += 1920) {
        int nb = k / 64;
        block_q2_0 *x = aligned_alloc(64, 4 * nb * sizeof(block_q2_0) + 64);
        block_q8_0 *y = aligned_alloc(64, 2 * nb * sizeof(block_q8_0) + 64);
        fill(x, 4 * nb, y, 2 * nb);
        q2y_t py[64]; prep_y(k, y, py);
        float a, b, c[4];
        dot_old(k, &a, x, y); dot_new(k, &b, x, py); dot_new4(k, c, x, nb * sizeof(block_q2_0), py);
        float r = dot_ref(k, x, y);
        printf("k=%d ref=%.6f old=%.6f new=%.6f new4=%.6f | fila3 ref=%.6f new4=%.6f\n", k, r, a, b, c[0], dot_ref(k, x + 3 * nb, y), c[3]);
    }
    // velocidad: en cache (mismo experto) y desde RAM (2 GiB de pesos)
    const size_t total = (size_t)2 << 30;
    struct { const char *name; int k, rows; } shp[] = { {"gate/up 640x2560", 2560, 640}, {"down 2560x640", 640, 2560} };
    for (int si = 0; si < 2; si++) {
        int k = shp[si].k, rows = shp[si].rows, nb = k / 64;
        size_t per = (size_t)rows * nb;
        size_t nmat = total / (per * sizeof(block_q2_0));
        size_t xbytes = (nmat * per * sizeof(block_q2_0) + (2<<20) - 1) & ~(size_t)((2<<20) - 1);
        block_q2_0 *X = aligned_alloc(2<<20, xbytes);
        if (getenv("HUGE")) madvise(X, xbytes, MADV_HUGEPAGE);
        block_q8_0 *Y = aligned_alloc(64, 2 * nb * sizeof(block_q8_0));
        fill(X, nmat * per, Y, 2 * nb);
        printf("%s:\n", shp[si].name);
        const char *mn[] = {"actual", "nuevo", "nuevo x4"};
        for (int mode = 1; mode < 2; mode++) {
            double l2 = run(mode, k, rows, 64, X, Y, 1, 20, 1);          // 64 veces el mismo rango chico -> cache
            double r1 = run(mode, k, rows, nmat, X, Y, 1, 2, 0);
            double r8 = run(mode, k, rows, nmat, X, Y, 8, 3, 0); double r4 = run(mode, k, rows, nmat, X, Y, 4, 3, 0); double r6 = run(mode, k, rows, nmat, X, Y, 6, 3, 0); printf("    4 hilos %.1f | 6 hilos %.1f\n", r4, r6);
            printf("  %-9s  1 hilo (cache L2/L3) %5.1f GB/s | 1 hilo RAM %5.1f | 8 hilos RAM %5.1f\n", mn[mode], l2, r1, r8);
        }
        free(X); free(Y);
    }
    return 0;
}
