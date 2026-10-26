"""Modelos de vídeo do Artlist presos a uma variante (Kling v3 720p sem som) e o avatar Fabric (imagem + áudio)."""

import asyncio
import json
import unittest
from pathlib import Path
from unittest import mock

from app import artlist as A


class Settings(unittest.TestCase):
    def test_kling_720_no_sound(self):
        st = A.video_settings(3143, "16:9", "1080p", 5, True, frames=True)
        self.assertEqual(st, {"resolution": "standard", "duration": 5, "aspect_ratio": "16:9", "generate_audio": False})
        self.assertEqual(A.video_group(3143), 349)
        self.assertEqual(A.video_group(349), 349)

    def test_fabric_has_no_duration(self):
        self.assertEqual(A.video_settings(370, "9:16", "480p", 8, True, frames=True),
                         {"resolution": "720p", "quality": "premium"})


class Request(unittest.TestCase):
    def run_video(self, group, frames, audios):
        seen = {}

        async def agent(instruction, schema, timeout_s, agent_model):
            seen["text"] = instruction
            return {"status": "failed", "message": "stub", "_calls": []}

        with mock.patch.object(A, "connected", return_value=True), mock.patch.object(A, "_run_agent", agent), \
                mock.patch.object(A, "share", side_effect=lambda p, **k: "tok"):
            with self.assertRaises(A.ArtlistError):
                asyncio.run(A.generate_video("talk", "16:9", frames, [], group, "720p", 5, False, 60,
                                             public_base="https://x", audios=audios))
        return seen.get("text", "")

    def test_fabric_sends_image_and_audio(self):
        text = self.run_video(370, [(Path("a.png"), "a"), (Path("b.png"), "b")], [(Path("f.mp3"), "falas")])
        self.assertIn('"modelGroupId": 370', text)
        self.assertIn("upload_audio", text)
        self.assertIn("exactly 2 items", text)
        self.assertNotIn("b.png", text)

    def test_fabric_needs_audio(self):
        with mock.patch.object(A, "connected", return_value=True):
            with self.assertRaisesRegex(A.ArtlistError, "áudio das falas"):
                asyncio.run(A.generate_video("t", "16:9", [(Path("a.png"), "a")], [], 370, "720p", 5, False, 60,
                                             public_base="https://x", audios=[]))

    def test_kling_variant_uses_real_group(self):
        text = self.run_video(3143, [(Path("a.png"), "a")], [])
        args = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
        self.assertEqual(args["modelGroupId"], 349)
        self.assertIs(args["settings"]["generate_audio"], False)


if __name__ == "__main__":
    unittest.main()
