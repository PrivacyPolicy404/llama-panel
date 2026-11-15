# Experimentos descartados

Cambios que funcionan y pasan `test-backend-ops`, pero no mejoraron el modelo de punta a punta en esta máquina.
No están en el fork; quedan acá por si sirven para otro modelo o placa. Detalle en `docs/analisis-rendimiento.md` 5.9.

- `iq4_xs-generic-gcn.diff`: en GCN, IQ4_XS usa el shader genérico de `mul_mat_vec` desde 3 columnas (el dedicado se
  desploma: n=3 579 -> 342 us, n=8 5793 -> 753 us en 4096x14336). Flash-Next con MTP: sin cambio (20.31 -> 20.37).
  Puede valer para modelos con mucho IQ4_XS denso y lotes de 3-8 tokens (MTP, varios slots).
