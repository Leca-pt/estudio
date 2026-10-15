"""Relevo das maquetes (app/terrain.py) e os tipos de objeto da natureza e dos modelos 3D (app/scene3d.py).

As alturas de referência foram calculadas pelo navegador (app/static/js/terrain.js): o servidor tem de dar exatamente o
mesmo número, senão as pessoas ficam a flutuar ou enterradas e a visibilidade calculada não bate com o que se vê.
"""

import unittest

from app import scene3d, terrain

TERRAIN = {"type": "terrain", "from": [-150, -200], "to": [150, 200], "z": 2, "relief": 3, "scale": 50, "seed": 7,
           "color": "rock", "features": [
               {"kind": "canyon", "path": [[-20, -200], [10, -60], [-15, 60], [20, 200]], "floor": 14, "width": 40,
                "depth": 45, "jagged": 0.6, "terraces": 0.5},
               {"kind": "mesa", "center": [90, 120], "radius": 45, "height": 25},
               {"kind": "hill", "center": [-90, -100], "radius": 50, "height": 12},
               {"kind": "ridge", "path": [[-140, 0], [-60, 40]], "width": 30, "height": 9},
               {"kind": "flat", "from": [60, -180], "to": [140, -120], "z": 1, "blend": 6}]}

# (x, y, altura calculada pelo navegador)
FROM_JS = [[0, 0, -42.83680614372715], [8, -50, -43.68768798889655], [90, 120, 27.9868694802045],
           [-90, -100, 13.058503967881203], [100, 150, 28.3110931978561], [-100, 20, 11.41699039869234],
           [100, -150, 1]]


class Heights(unittest.TestCase):
    def test_same_as_browser(self):
        for x, y, h in FROM_JS:
            self.assertEqual(terrain.height(TERRAIN, x, y), h, (x, y))

    def test_ground_outside_is_zero(self):
        g = terrain.ground_fn([{"objects": [TERRAIN]}])
        self.assertEqual(g(500, 500), 0.0)
        self.assertLess(g(8, -50), -40)  # fundo do canyon

    def test_flat_world_has_no_ground_function(self):
        self.assertIsNone(terrain.ground_fn([{"objects": [{"type": "box"}]}]))

    def test_canyon_wall_blocks_sight(self):
        g = terrain.ground_fn([{"objects": [TERRAIN]}])
        floor = g(8, -50)
        # do planalto, ao longe e à altura dos olhos, não se vê quem está no fundo do canyon
        self.assertTrue(terrain.blocks_sight(g, [-100, -50, g(-100, -50) + 1.6], [8, -50, floor + 1.5]))
        # dentro do canyon, ao longo do fundo, vê-se
        self.assertFalse(terrain.blocks_sight(g, [4, -35, g(4, -35) + 1.6], [8, -50, floor + 1.5]))

    def test_summary_lists_heights(self):
        text = terrain.summary(TERRAIN)
        self.assertIn("canyon", text)
        self.assertIn("fundo do canyon", text)


class Validation(unittest.TestCase):
    def test_terrain_defaults_written(self):
        out = scene3d.validate_set({"objects": [{"type": "terrain", "from": [0, 0], "to": [100, 100],
                                                 "features": [{"kind": "canyon", "path": [[0, 50], [100, 50]]}]}]})
        t = out["objects"][0]
        self.assertEqual(t["features"][0]["floor"], 8)
        self.assertEqual(t["features"][0]["width"], 24)
        self.assertIn("jagged", t["features"][0])

    def test_bad_feature(self):
        with self.assertRaises(scene3d.SceneError):
            scene3d.validate_set({"objects": [{"type": "terrain", "from": [0, 0], "to": [10, 10],
                                               "features": [{"kind": "volcano"}]}]})

    def test_scatter_limit(self):
        with self.assertRaises(scene3d.SceneError):
            scene3d.validate_set({"objects": [{"type": "scatter", "item": "rock", "from": [0, 0], "to": [9, 9],
                                               "count": 4000}] * 2})

    def test_rock_and_model(self):
        out = scene3d.validate_set({"objects": [
            {"type": "rock", "center": [1, 2], "shape": "spire"},
            {"type": "model", "asset": "prj_mota", "center": [0, 0, 0], "height": 1.1}]})
        self.assertEqual(out["objects"][0]["shape"], "spire")
        self.assertEqual(out["objects"][1]["asset"], "PRJ_MOTA")

    def test_new_pieces_exist(self):
        for name in ("bookshelf", "toilet", "bathtub", "piano", "office_chair", "curtain", "frame"):
            self.assertIn(name, scene3d.PIECES)

    def test_help_example_is_valid(self):
        import json

        text = scene3d.SET_HELP.split("Exemplo (canyon com um rio seco e rochas): ")[1]
        example = json.JSONDecoder().raw_decode(text)[0]
        out = scene3d.validate_set(example)
        self.assertEqual(out["objects"][0]["type"], "terrain")


if __name__ == "__main__":
    unittest.main()
