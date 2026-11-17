"""Parseo de logs de llama-server y KoboldCpp (lineas reales de journalctl)."""
import unittest
from unittest.mock import patch

from llama_panel import stats

ROUTER_LOG = """\
1794625291.917731 god llama-server[35078]: [38701] 0.00.024.682 I srv    load_model: loading model '/models/Qwen3.5-9B-Q4_K_M.gguf'
1794625300.000001 god llama-server[35078]: [38701] 0.13.127.686 I slot print_timing: id  0 | task 0 | prompt eval time =    2591.48 ms /   465 tokens (    5.57 ms per token,   179.43 tokens per second)
1794625300.000002 god llama-server[35078]: [38701] 0.13.127.690 I slot print_timing: id  0 | task 0 |        eval time =    7875.48 ms /   184 tokens (   43.04 ms per token,    23.24 tokens per second)
1794625300.000003 god llama-server[35078]: [38701] 0.13.127.692 I slot print_timing: id  0 | task 0 |       total time =   10466.96 ms /   649 tokens
1794625300.000004 god llama-server[35078]: [38701] 0.46.030.791 I slot print_timing: id  0 | task 0 | draft acceptance = 0.58824 (  320 accepted /   544 generated), mean len =  2.18
1794625310.000000 god llama-server[35078]: [40617] 0.00.023.352 I srv    load_model: loading model '/models/otro.gguf'
1794625320.000000 god llama-server[35078]: [40617] 0.26.280.729 I slot print_timing: id  0 | task 187 | prompt eval time =     329.26 ms /    36 tokens (    9.15 ms per token,   109.34 tokens per second)
1794625320.500000 god llama-server[35078]: [40617] 0.26.280.743 I slot print_timing: id  0 | task 187 |        eval time =    5819.09 ms /   136 tokens (   43.10 ms per token,    23.20 tokens per second)
linea basura sin formato
"""

KOBOLD_LOG = [
    (1.0, "[16:50:41] Generating Image (27 steps)"),
    (2.0, "Decoding: 1/27 steps, 3.48 s/it\x1b[KDecoding: 27/27 steps, 3.06 s/it\x1b[K"),
    (3.0, "[16:52:12] Generating Media Complete"),
    (4.0, "[23:59:30] Generating Image (20 steps)"),
    (5.0, "[00:00:05] Generating Media Complete"),
    (6.0, "[00:01:00] Generating Media Complete"),  # fin sin inicio: se ignora
]


class TextStats(unittest.TestCase):
    def setUp(self):
        entries = stats.parse_journal_unix(ROUTER_LOG)
        self.reqs = stats.parse_text_stats(entries, {"/models/Qwen3.5-9B-Q4_K_M.gguf": "qwen"})

    def test_one_entry_per_request(self):
        self.assertEqual(len(self.reqs), 2)

    def test_speeds_and_tokens(self):
        r = self.reqs[0]
        self.assertEqual((r["p_tok"], r["p_tps"], r["g_tok"], r["g_tps"]), (465, 179.43, 184, 23.24))

    def test_model_from_pid(self):
        self.assertEqual(self.reqs[0]["model"], "qwen")
        self.assertEqual(self.reqs[1]["model"], "otro")  # sin id conocido: nombre del archivo

    def test_draft_acceptance(self):
        self.assertEqual(self.reqs[0]["draft"], {"rate": 0.58824, "acc": 320, "gen": 544})
        self.assertIsNone(self.reqs[1]["draft"])

    def test_timestamp_is_end_of_generation(self):
        self.assertEqual(self.reqs[1]["t"], 1794625320.5)


class ImgStats(unittest.TestCase):
    def test_durations(self):
        gens = stats.parse_img_stats(KOBOLD_LOG)
        self.assertEqual([(g["steps"], g["secs"]) for g in gens], [(27, 91), (20, 35)])  # cruza medianoche


class Logs(unittest.TestCase):
    def test_progress_bar_keeps_last_state(self):
        self.assertEqual(stats.clean_log_line("a\x1b[Kb 2/3\x1b[K"), "b 2/3")

    def test_strips_ansi(self):
        self.assertEqual(stats.clean_log_line("\x1b[32mok\x1b[0m"), "ok")

    def test_collapse_consecutive_progress(self):
        lines = ["inicio", "Decoding: 1/27 steps, 3 s/it", "Decoding: 2/27 steps, 3 s/it",
                 "Decoding: 1/21 steps, 2 it/s", "", "fin"]
        self.assertEqual(stats.collapse_progress(lines),
                         ["inicio", "Decoding: 2/27 steps, 3 s/it", "Decoding: 1/21 steps, 2 it/s", "fin"])

class LiveTest(unittest.TestCase):
    LINES = [
        (100.0, "[7] srv load_model: initializing, n_slots = 1, n_ctx_slot = 32768, kv_unified = 'false'"),
        (101.0, "[7] 0.53.8 I slot print_timing: id  0 | task 177 | prompt processing, n_tokens =   1216, progress = 0.37, t =   9.26 s / 131.25 tokens per second"),
        (110.0, "[7] 1.10.6 I slot print_timing: id  0 | task 177 | n_gen =    101, tg =  16.96 t/s, tg_3s =  17.13 t/s"),
    ]
    END = [
        (112.0, "[7] 1.10.6 I slot print_timing: id  0 | task 177 | draft acceptance = 0.69444 (   25 accepted /    36 generated), mean len =  2.39"),
        (112.1, "[7] 1.10.6 I slot      release: id  0 | task 177 | stop processing: n_tokens = 3307, truncated = 0"),
    ]

    def test_generating(self):
        live = stats.parse_live(self.LINES, now=112.0)
        self.assertEqual(live["state"], "gen")
        self.assertEqual((live["n_gen"], live["tps"], live["ctx_size"]), (101, 17.13, 32768))
        self.assertEqual(live["gen_avg_tps"], 16.96)

    def test_prompt_progress(self):
        live = stats.parse_live(self.LINES[:2], now=102.0)
        self.assertEqual(live["state"], "prompt")
        self.assertAlmostEqual(live["prompt_progress"], 0.37)
        self.assertEqual(live["prompt_tokens"], 1216)

    def test_finished_and_stale(self):
        live = stats.parse_live(self.LINES + self.END, now=113.0)
        self.assertEqual(live["state"], "idle")
        self.assertEqual((live["ctx_used"], live["draft_rate"], live["draft_len"]), (3307, 0.69444, 2.39))
        stale = stats.parse_live(self.LINES, now=200.0)
        self.assertEqual(stale["state"], "gen")
        self.assertTrue(stale["stale"])  # falta de logs no es fin de tarea

    def test_long_prefill_keeps_bars_between_slow_batches(self):
        first = stats.parse_live(self.LINES[:2], now=102)
        gap = stats.parse_live([], now=125, previous=first)
        self.assertEqual((gap["state"], gap["prompt_tokens"], gap["ctx_size"]), ("prompt", 1216, 32768))
        self.assertTrue(gap["stale"])
        next_batch = [(126, self.LINES[1][1].replace("1216", "2048").replace("0.37", "0.62"))]
        resumed = stats.parse_live(next_batch, now=127, previous=gap)
        self.assertFalse(resumed["stale"])
        self.assertEqual(resumed["ctx_used"], 2048)

    def test_incremental_generation_retains_context_and_prompt_speed(self):
        prompt = stats.parse_live(self.LINES[:2], now=102)
        gen = stats.parse_live(self.LINES[2:], now=112, previous=prompt)
        self.assertEqual((gen["ctx_size"], gen["prompt_tps"], gen["ctx_used"]), (32768, 131.25, 3387))
        self.assertEqual(gen["prompt_progress"], 1)

    def test_new_task_clears_previous_request_fields(self):
        old = stats.parse_live(self.LINES + self.END, now=113)
        new = stats.parse_live([(120, "[7] srv launch_slot_: id 0 | task 200 | processing task, is_child = 0")],
                               now=121, previous=old)
        self.assertEqual((new["ctx_size"], new["ctx_used"], new["prompt_progress"]), (32768, 0, 0))
        self.assertIsNone(new["gen_avg_tps"])
        self.assertIsNone(new["prompt_tps"])

    def test_unload_discards_context_and_inflight_state(self):
        old = stats.parse_live(self.LINES, now=112)
        gone = stats.parse_live([(120, "srv run: instance name=model exited with status 1")], now=121, previous=old)
        self.assertEqual(gone["state"], "idle")
        self.assertIsNone(gone["ctx_size"])

    def test_final_timings_without_periodic_generation_log(self):
        entries = stats.parse_journal_unix(ROUTER_LOG)
        done = stats.parse_live(entries[:5], now=1794625301)
        self.assertEqual((done["prompt_tps"], done["gen_avg_tps"], done["n_gen"]), (179.43, 23.24, 184))

    def test_polling_window_can_drop_initial_context_line(self):
        def journal_text(lines):
            return "\n".join(f"{t} host llama-server[1]: {line}" for t, line in lines)
        cache = {"t": 0, "data": None, "last_entry": 0}
        with patch.object(stats, "_live", cache), patch.object(stats.time, "time", return_value=102) as clock, \
                patch.object(stats, "journal", return_value=journal_text(self.LINES[:2])) as read, \
                patch.object(stats, "router_models", return_value={}):
            first = stats.live()
            self.assertEqual(first["ctx_size"], 32768)
            clock.return_value = 112
            read.return_value = journal_text(self.LINES[2:])
            gen = stats.live()
            self.assertEqual((gen["ctx_size"], gen["prompt_tps"], gen["gen_avg_tps"]), (32768, 131.25, 16.96))
            clock.return_value = 125
            unchanged = stats.live()
            self.assertTrue(unchanged["stale"])
            self.assertEqual(unchanged["ctx_used"], gen["ctx_used"])

    def test_cached_prompt_uses_slot_total_instead_of_new_tokens_only(self):
        live = stats.parse_live(self.LINES, now=112)
        live.update(task_id="7:270", state="prompt", n_gen=None, prompt_tokens=4416, prompt_progress=0.97,
                    prompt_log_tokens=4416, prompt_log_progress=0.97)
        slot = {"is_processing": True, "id_task": 270, "n_ctx": 65536, "n_prompt_tokens": 65296,
                "n_prompt_tokens_processed": 4416, "n_prompt_tokens_cache": 58836, "next_token": [{"n_decoded": 0}]}
        merged = stats.merge_live_slot(live, slot)
        # contexto = lo ya procesado (caché + nuevos), no la tanda en curso entera (65296): esa hacía saltar la barra
        self.assertEqual((merged["ctx_used"], merged["prompt_cached"]), (63252, 58836))
        self.assertEqual(merged["prompt_total"], round(63252 / 0.97))
        self.assertAlmostEqual(merged["prompt_progress"], 63252 / merged["prompt_total"])
        self.assertEqual(live["ctx_used"], 3387)  # no mutar el snapshot anterior
        slot["id_task"] = 271
        self.assertEqual(stats.merge_live_slot(live, slot), live)

    def test_prompt_moves_smoothly_between_batches(self):
        base = {"task_id": "7:459", "state": "prompt", "task_start": 100.0, "prompt_tps_prev": 200.0,
                "prompt_cached": 0, "batch_seen": None, "batch_t": None, "prompt_est": None}
        slot = {"is_processing": True, "id_task": 459, "n_ctx": 8192, "n_prompt_tokens": 2048,
                "n_prompt_tokens_processed": 0, "n_prompt_tokens_cache": 0, "next_token": [{"n_decoded": 0}]}
        # primera tanda, sin total: avanza con la velocidad del pedido anterior y no inventa un porcentaje
        a = stats.merge_live_slot(base, slot, now=105.0)
        self.assertEqual((a["ctx_used"], a["prompt_tokens"], a["prompt_progress"]), (1000, 1000, None))
        b = stats.merge_live_slot(a, slot, now=120.0)
        self.assertEqual(b["ctx_used"], 2048)  # tope: la tanda en curso
        # terminó la tanda (el log ya dio el total y la velocidad media): la segunda arranca al verlo
        b.update(prompt_log_tokens=2048, prompt_log_elapsed=8.0, prompt_log_progress=0.53)
        slot.update(n_prompt_tokens=3864, n_prompt_tokens_processed=2048)
        c = stats.merge_live_slot(b, slot, now=121.0)
        self.assertEqual(c["ctx_used"], 2048)
        d = stats.merge_live_slot(c, slot, now=124.0)
        self.assertEqual(d["ctx_used"], 2048 + 768)  # 256 tok/s × 3 s
        self.assertAlmostEqual(d["prompt_progress"], 2816 / d["prompt_total"])
        # el pedido siguiente arranca su primera tanda con la velocidad de esta (no con la de un pedido corto)
        nxt = stats.parse_live([(130.0, "[7] srv launch_slot_: id 0 | task 460 | processing task, is_child = 0")],
                               now=130.0, previous=d)
        self.assertEqual(nxt["batch_rate_prev"], 256.0)
        # nunca para atrás, aunque cambie la velocidad estimada

        d["prompt_log_elapsed"] = 30.0
        self.assertEqual(stats.merge_live_slot(d, slot, now=124.5)["ctx_used"], 2816)

    def test_live_keeps_previous_prompt_speed_for_short_requests(self):
        launch = lambda t, task: (t, f"[7] srv launch_slot_: id 0 | task {task} | processing task, is_child = 0")
        timing = (119, "[7] slot print_timing: id 0 | task 200 | prompt eval time = 1170.28 ms / 19 tokens"
                       " ( 61.59 ms per token, 16.24 tokens per second)")
        first = stats.parse_live([launch(100, 200), timing], now=119)
        self.assertEqual(first["prompt_tps"], 16.24)
        # el pedido siguiente todavía no informó nada: se conserva la velocidad del anterior, aparte
        second = stats.parse_live([launch(120, 201)], now=125, previous=first)
        self.assertIsNone(second["prompt_tps"])
        self.assertEqual((second["prompt_tps_prev"], second["prompt_prev_task"]), (16.24, "7:200"))
        self.assertEqual(second["prompt_elapsed"], 5)  # corre sin esperar un log
        third = stats.parse_live([launch(130, 202)], now=131, previous=second)
        self.assertEqual((third["prompt_tps_prev"], third["prompt_prev_task"]), (16.24, "7:200"))
        other_model = stats.parse_live([(140, launch(140, 1)[1].replace("[7]", "[8]"))], now=141, previous=third)
        self.assertIsNone(other_model["prompt_tps_prev"])

    def test_live_slot_confirms_request_is_alive(self):
        live = stats.parse_live(self.LINES[:2], now=130)
        self.assertTrue(live["stale"])
        task = live["task_id"].rsplit(":", 1)[-1]
        slot = {"is_processing": True, "id_task": int(task), "n_prompt_tokens": 10,
                "n_prompt_tokens_processed": 10, "n_prompt_tokens_cache": 0}
        merged = stats.merge_live_slot(live, slot)
        self.assertFalse(merged["stale"])
        self.assertEqual(merged["age"], 0)
        # la salida avanza con /slots entre un log y el siguiente
        gen = stats.merge_live_slot({**live, "n_gen": 40}, {**slot, "next_token": [{"n_decoded": 57}]})
        self.assertEqual((gen["state"], gen["n_gen"]), ("gen", 57))

    def test_task_numbers_restart_in_a_new_process(self):
        new_proc = [(120.0, "[9] 0.28.3 I slot print_timing: id  0 | task 177 | n_gen =    40, tg =  15.0 t/s, tg_3s =  15.5 t/s")]
        self.assertEqual(stats.parse_live(self.LINES + self.END + new_proc, now=121.0)["state"], "gen")


if __name__ == "__main__":
    unittest.main()
