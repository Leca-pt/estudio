"""Áudio de falas: um ficheiro cobre linhas seguidas; fica desatualizado se o guião mudar."""

import unittest
from types import SimpleNamespace as NS

from app import line_audio as LA


def lines(*texts):
    return [NS(id=10 + n, text=t) for n, t in enumerate(texts)]


class Range(unittest.TestCase):
    def test_range_in_order_and_reversed(self):
        ls = lines("a", "b", "c", "d")
        self.assertEqual([x.id for x in LA.line_range(ls, 11, 13)], [11, 12, 13])
        self.assertEqual([x.id for x in LA.line_range(ls, 13, 11)], [11, 12, 13])
        self.assertEqual([x.id for x in LA.line_range(ls, 12, None)], [12])
        with self.assertRaises(ValueError):
            LA.line_range(ls, 99, None)


class Problem(unittest.TestCase):
    def setUp(self):
        self.ls = lines("a", "b", "c", "d")
        self.m = NS(lines=[11, 12], basis=LA.basis(self.ls[1:3]))

    def test_ok(self):
        self.assertIsNone(LA.problem(self.m, self.ls))

    def test_text_changed(self):
        self.ls[2].text = "c2"
        self.assertIn("texto", LA.problem(self.m, self.ls))

    def test_deleted_and_moved(self):
        self.assertIn("apagada", LA.problem(self.m, [x for x in self.ls if x.id != 12]))
        moved = [self.ls[1], self.ls[0], self.ls[3], self.ls[2]]
        self.assertIn("seguidas", LA.problem(self.m, moved))


class FileName(unittest.TestCase):
    def test_names(self):
        p = NS(prefix="PRJ")
        self.assertEqual(LA.file_name(p, "T1E01_C02", {"first": 3, "last": 3}, ".mp3"), "PRJ_T1E01_C02_L03_audio.mp3")
        self.assertEqual(LA.file_name(p, "T1E01_C02", {"first": 3, "last": 5}, ".wav"), "PRJ_T1E01_C02_L03-05_audio.wav")


if __name__ == "__main__":
    unittest.main()
