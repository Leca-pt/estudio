"""Catálogo de vídeo da fal: o modelo tirado do esquema de entrada e o preço tirado do texto publicado."""

import unittest

from app import fal_video_catalog as C
from app.fal_catalog import Unusable


def schema(**props):
    required = props.pop("_required", ["prompt"])
    return {"properties": {"prompt": {"type": "string"}, **props}, "required": required}


class FromSchema(unittest.TestCase):
    def test_image_to_video(self):
        m = C.model_from_schema("x/i2v", "X", schema(
            image_url={"type": "string"}, end_image_url={"type": "string"}, generate_audio={"type": "boolean"},
            duration={"enum": ["5", "10"]}, resolution={"enum": ["540p", "720p", "1080p"]},
            aspect_ratio={"enum": ["16:9", "9:16"]}, _required=["prompt", "image_url"]))
        self.assertEqual((m["durations"], m["dur_kind"], m["res"]), ([5, 10], "str", ["720p", "1080p"]))
        self.assertTrue(m["end"] and m["audio"] and not m.get("refs") and not m["video_refs"])
        ep, args = C.request(m, "frames", "p", "4:3", ["a", "b"], [], 10, "720p", False)
        self.assertEqual(args, {"prompt": "p", "image_url": "a", "end_image_url": "b", "duration": "10",
                                "resolution": "720p", "aspect_ratio": "16:9", "generate_audio": False})

    def test_references_with_video(self):
        m = C.model_from_schema("x/r2v", "X", schema(
            image_urls={"type": "array", "maxItems": 7, "description": "Referenced as @Image1"},
            video_urls={"anyOf": [{"type": "array", "maxItems": 2}, {"type": "null"}]},
            duration={"type": "integer", "minimum": 3, "maximum": 12}))
        self.assertEqual((m["max_images"], m["max_videos"], m["ref_format"]), (7, 2, "@Image{n}"))
        ep, args = C.request(m, "references", "p", "16:9", list("abcdefghi"), ["v"], 8, "720p", True)
        self.assertEqual((len(args["image_urls"]), args["video_urls"], args["duration"]), (7, ["v"], 8))

    def test_unusable(self):
        with self.assertRaises(Unusable):
            C.model_from_schema("x", "X", schema(video_url={"type": "string"}, _required=["prompt", "video_url"]))
        with self.assertRaises(Unusable):  # exige sempre a imagem final
            C.model_from_schema("x", "X", schema(start_image_url={}, end_image_url={},
                                                  _required=["prompt", "start_image_url", "end_image_url"]))
        with self.assertRaises(Unusable):
            C.model_from_schema("x", "X", schema(image_url={}, mask_url={}, _required=["prompt", "image_url", "mask_url"]))


class Pricing(unittest.TestCase):
    CASES = [
        ("Billing is calculated per second of video generated. Rates without audio / with audio are 360p: $0.025 / $0.035; 720p: $0.045 / $0.060; 1080p: $0.090 / $0.115.",
         {"price_s": {"720p": 0.06, "1080p": 0.115}}),
        ("Priced per second of output video, by resolution: 480p at $0.08/sec, 720p at $0.14/sec, 1080p at $0.25/sec.",
         {"price_s": {"480p": 0.08, "720p": 0.14, "1080p": 0.25}}),
        ("Billing is calculated per second of output video. For 360p, your request will cost $0.03 per second; for "
         "720p, $0.10 per second; for 1080p, $0.15 per second.", {"price_s": {"720p": 0.1, "1080p": 0.15}}),
        ("Your request will be charged at 0.17 $ per second of generated video at 720p, 0.29 $ per second at 1080p",
         {"price_s": {"720p": 0.17, "1080p": 0.29}}),
        ("A 6s 480p video will cost $0.302. At 480p, every second costs $0.05, and at 720p, every second costs $0.07.",
         {"price_s": {"480p": 0.05, "720p": 0.07}}),
        ("Your request will cost $0.49 per video generation.", {"price_flat": 0.49}),
        ("You will be charged $0.112 (audio off) or $0.168 (audio on) per second. For example, a 5s video costs $0.84",
         {"price_s": {"480p": 0.168, "720p": 0.168, "1080p": 0.168}}),
    ]

    def test_texts(self):
        for text, want in self.CASES:
            with self.subTest(text=text[:40]):
                self.assertEqual(C.parse_pricing(text), want)

    def test_floor_guard(self):
        m = {"res": ["720p"]}
        C.apply_price(m, {"unit": "seconds", "unit_price": 0.05}, "720p: $0.01 per second")
        self.assertEqual((m["price_s"], m["price_src"]), ({"720p": 0.05}, "a partir de (API da fal)"))


if __name__ == "__main__":
    unittest.main()
