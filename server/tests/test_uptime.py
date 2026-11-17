"""Staying up for a crowd, and saying so, briefly, when it is not."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import down_alert                   # noqa: E402
from market import web              # noqa: E402

DOWN = [False] * 3


def checks(results, start=0, state=None):
    state, sent = dict(state or {}), []
    for i, ok in enumerate(results):
        state, message = down_alert.step(state, ok, start + 60 * i, "timeout")
        if message:
            sent.append((i, message))
    return state, sent


class DownAlert(unittest.TestCase):
    def test_a_blip_or_a_restart_says_nothing(self):
        self.assertEqual(checks([False, False, True, False, True])[1], [])

    def test_an_outage_is_one_message_and_its_end_another(self):
        _, sent = checks([True, False, False, False, False, False, True, True])
        self.assertEqual([i for i, _ in sent], [3, 6], "said on the third failed minute, and when it answers again")
        self.assertTrue(sent[0][1].startswith("DSM is down"))
        self.assertEqual(sent[1][1], "DSM is back, after about 5 min down.")

    def test_a_flapping_site_is_one_message_an_hour(self):
        state, sent = checks(DOWN + [True] + DOWN + [True] + DOWN + [True])
        self.assertEqual([m.split(",")[0].split(":")[0] for _, m in sent], ["DSM is down", "DSM is back"],
                         "outages inside the hour say nothing, and an outage nobody was told of has no back message")
        _, later = checks(DOWN, start=4000, state=state)
        self.assertEqual(len(later), 1, "after an hour a new outage is announced again")


class Server(unittest.TestCase):
    def test_the_server_lets_a_crowd_wait(self):
        # 2026-11-11: Python's 5 waiting connections stalled 10 players by a second and reset 50.
        self.assertGreaterEqual(web.Server.request_queue_size, 128)
        self.assertTrue(web.Server.daemon_threads)


if __name__ == "__main__":
    unittest.main()
