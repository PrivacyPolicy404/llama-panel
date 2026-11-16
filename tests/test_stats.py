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
        self.assertEqual((gen["ctx_size"], gen["prompt_tps"], gen["ctx_used"]), (32768, 131.25, 1317))
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
                patch.object(stats, "journal", return_value=journal_text(self.LINES[:2])) as read:
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

    def test_task_numbers_restart_in_a_new_process(self):
        new_proc = [(120.0, "[9] 0.28.3 I slot print_timing: id  0 | task 177 | n_gen =    40, tg =  15.0 t/s, tg_3s =  15.5 t/s")]
        self.assertEqual(stats.parse_live(self.LINES + self.END + new_proc, now=121.0)["state"], "gen")


if __name__ == "__main__":
    unittest.main()
