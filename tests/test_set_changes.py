"""Variações da maquete do local só numa cena (set_changes da encenação) e peças inclinadas (rot_x, rot_y)."""

import unittest

from app import layout_check, scene3d

BASE = scene3d.validate_set({"objects": [
    {"type": "zone", "name": "sala", "from": [-3, -3], "to": [3, 3]},
    {"type": "piece", "model": "table_rect", "center": [0, 0, 0], "size": [1.2, 0.7, 0.75], "label": "mesa 1"},
    {"type": "piece", "model": "chair", "center": [-0.3, -0.6, 0]},
    {"type": "piece", "model": "chair", "center": [0.3, 0.6, 0], "rot_z": 180},
]})


def flipped(x, y, rot_z=0, z=0.75):
    return {"type": "piece", "model": "chair", "pos": [x, y, z], "rot": [180, 0, rot_z], "label": "virada"}


class Validate(unittest.TestCase):
    def test_aliases(self):
        out = scene3d.validate_set_changes({"update": [{"i": 2, "pos": [1, 2, 0.75], "rot": [180, 0, 90]}],
                                            "add": [flipped(0.3, 0.2)], "note": "fechada"})
        self.assertEqual(out["update"][0]["center"], [1, 2, 0.75])
        self.assertEqual((out["update"][0]["rot_x"], out["update"][0]["rot_z"]), (180, 90))
        self.assertEqual(out["add"][0]["rot_x"], 180)
        self.assertNotIn("rot_y", out["add"][0])  # 0 não se guarda

    def test_errors(self):
        for bad in ({"zap": 1}, {"hide": ["a"]}, {"update": [{"i": 2, "type": "box"}]}, {"update": [{"i": 2}]}):
            with self.assertRaises(scene3d.SceneError):
                scene3d.validate_set_changes(bad)
        self.assertIsNone(scene3d.validate_set_changes({"note": "só nota"}))

    def test_indices_checked_on_apply(self):
        with self.assertRaises(scene3d.SceneError):
            scene3d.apply_set_changes(BASE, scene3d.validate_set_changes({"hide": [99]}))
        with self.assertRaises(scene3d.SceneError):
            scene3d.apply_set_changes(BASE, scene3d.validate_set_changes({"hide": [2], "update": [{"i": 2, "rot_z": 9}]}))


class Apply(unittest.TestCase):
    def test_numbers_kept_and_base_untouched(self):
        changes = scene3d.validate_set_changes({"hide": [2, 3], "add": [flipped(-0.3, -0.2, 180), flipped(0.3, 0.2)]})
        out = scene3d.apply_set_changes(BASE, changes)
        self.assertEqual([o["n"] for o in out["objects"]], [0, 1, 4, 5])
        self.assertTrue(out["objects"][-1]["scene_only"])
        self.assertEqual(len(BASE["objects"]), 4)
        self.assertNotIn("n", BASE["objects"][0])

    def test_update_moves_only_in_scene(self):
        out = scene3d.apply_set_changes(BASE, scene3d.validate_set_changes({"update": [{"i": 2, "pos": [-0.3, -0.2, 0.75], "rot": [180, 0, 180]}]}))
        self.assertEqual(out["objects"][2]["center"], [-0.3, -0.2, 0.75])
        self.assertEqual(BASE["objects"][2]["center"], [-0.3, -0.6, 0])

    def test_summary(self):
        changes = scene3d.validate_set_changes({"hide": [2, 3], "add": [flipped(0.3, 0.2)], "note": "fechada"})
        text = scene3d.set_changes_summary(changes, BASE)
        self.assertIn("fechada", text)
        self.assertIn("sem 2 chair", text)
        self.assertEqual(scene3d.set_changes_count(changes), 3)


class Tilt(unittest.TestCase):
    def test_tilted_size(self):
        self.assertEqual(scene3d.tilted_size([0.45, 0.48, 0.9], {"rot_x": 180}), [0.45, 0.48, 0.9])
        self.assertEqual(scene3d.tilted_size([0.45, 0.48, 0.9], {"rot_x": 90}), [0.45, 0.9, 0.48])
        self.assertEqual(scene3d.tilted_size([0.45, 0.48, 0.9], {"rot_y": 90}), [0.9, 0.48, 0.45])

    def test_flipped_seat_rests_on_seat(self):
        o = scene3d.validate_set({"objects": [flipped(0, 0)]})["objects"][0]
        self.assertAlmostEqual(scene3d.flipped_seat_h(o), 0.46)
        self.assertIsNone(scene3d.flipped_seat_h({**o, "rot_x": 90}))


class Check(unittest.TestCase):
    def _warnings(self, adds):
        out = scene3d.apply_set_changes(BASE, scene3d.validate_set_changes({"hide": [2, 3], "add": adds}))
        return "\n".join(layout_check.check(out))

    def test_chairs_on_table_pass(self):
        text = self._warnings([flipped(-0.3, -0.2, 180), flipped(0.3, 0.2)])
        self.assertNotIn("virada", text)

    def test_sunk_and_floating(self):
        text = self._warnings([flipped(0, 0, z=0.5), flipped(-2, 2, z=1.4)])
        self.assertIn("#4 chair «virada» ocupam", text.replace("#1 table_rect «mesa 1» e ", ""))
        self.assertIn("#5 chair «virada» começa a 1.40 m", text)


if __name__ == "__main__":
    unittest.main()
