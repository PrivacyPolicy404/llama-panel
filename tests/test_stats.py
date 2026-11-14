"""Parseo de logs de llama-server y KoboldCpp (lineas reales de journalctl)."""
import unittest

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


if __name__ == "__main__":
    unittest.main()
