"""
tests/test_animation_sender.py

Unit and integration tests for:
1. Media hashing & metadata probing (FFmpeg/ffprobe/PIL/cv2)
2. High-quality H.264 MP4 conversion with quality ladder
3. Telegram file_id SQLite caching & deduplication
4. Telegram send_high_quality_animation pipeline
"""

import os
import io
import sys
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from services.animation_sender import (
    compute_media_hash,
    probe_media,
    convert_to_high_quality_mp4,
    send_high_quality_animation,
    MediaMetadata
)
from services.graphics import fc_card_generator


class TestAnimationPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        database.init_db()

    def _create_sample_frames(self, count=10, width=480, height=680) -> list[Image.Image]:
        frames = []
        for i in range(count):
            img = Image.new("RGB", (width, height), (i * 20 % 255, 50, 150))
            draw = ImageDraw.Draw(img)
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline=(255, 255, 255), width=3)
            draw.text((width // 2 - 30, height // 2), f"Frame {i}", fill=(255, 255, 255))
            frames.append(img)
        return frames

    def _create_sample_gif(self, count=10, width=480, height=680) -> io.BytesIO:
        frames = self._create_sample_frames(count, width, height)
        buf = io.BytesIO()
        frames[0].save(
            buf,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=40,
            loop=0
        )
        buf.seek(0)
        return buf

    def test_compute_media_hash_deterministic(self):
        frames1 = self._create_sample_frames(5)
        frames2 = self._create_sample_frames(5)
        
        h1 = compute_media_hash(frames1)
        h2 = compute_media_hash(frames2)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64) # Valid SHA-256

        raw_bytes = b"sample_video_data_12345"
        h_bytes = compute_media_hash(raw_bytes)
        self.assertEqual(len(h_bytes), 64)

    def test_database_media_cache_crud(self):
        import uuid
        test_hash = f"test_hash_{uuid.uuid4().hex}"
        test_file_id = f"BAACAgIAAxkBAAICamF7_{uuid.uuid4().hex}"

        # Initially None
        self.assertIsNone(database.get_cached_telegram_media(test_hash, "animation"))

        # Save to DB
        database.save_cached_telegram_media(test_hash, test_file_id, "animation")

        # Retrieve from DB
        cached = database.get_cached_telegram_media(test_hash, "animation")
        self.assertEqual(cached, test_file_id)

    def test_probe_media_frames_and_gif(self):
        frames = self._create_sample_frames(12, 480, 680)
        meta = probe_media(frames)
        self.assertEqual(meta.width, 480)
        self.assertEqual(meta.height, 680)
        self.assertEqual(meta.fps, 24.0)
        self.assertAlmostEqual(meta.duration, 12 / 24.0, places=2)

        gif_buf = self._create_sample_gif(8, 300, 300)
        meta_gif = probe_media(gif_buf)
        self.assertEqual(meta_gif.width, 300)
        self.assertEqual(meta_gif.height, 300)

    def test_convert_to_high_quality_mp4(self):
        frames = self._create_sample_frames(8, 480, 680)
        mp4_path, meta, is_temp = convert_to_high_quality_mp4(
            input_source=frames,
            fps=24.0,
            initial_crf=16
        )
        try:
            self.assertTrue(os.path.exists(mp4_path))
            self.assertGreater(os.path.getsize(mp4_path), 500)
            self.assertEqual(meta.width, 480)
            self.assertEqual(meta.height, 680)
        finally:
            if is_temp and os.path.exists(mp4_path):
                os.remove(mp4_path)

    def test_fc_card_generator_render_frames(self):
        player_data = {
            "player_name": "VINICIUS JR",
            "team_name": "Реал Мадрид",
            "position": "LW",
            "total_goals": 15,
            "total_assists": 10,
            "matches_played": 14,
        }
        frames, fps, anim_w, anim_h = fc_card_generator.render_animated_card_frames(player_data, "toty_gold")
        self.assertEqual(len(frames), 24)
        self.assertEqual(fps, 24.0)
        self.assertEqual(anim_w, 480)
        self.assertEqual(anim_h, 680)

    def test_send_high_quality_animation_first_and_second_dispatch(self):
        async def _run():
            mock_bot = AsyncMock()
            
            # Setup mock return from send_animation
            mock_result = MagicMock()
            mock_result.animation = MagicMock()
            mock_result.animation.file_id = "BAACAgIA_TEST_DYNAMIC_FILE_ID_9999"
            mock_bot.send_animation.return_value = mock_result

            test_frames = self._create_sample_frames(6, 480, 680)
            test_hash = compute_media_hash(test_frames)

            # 1. First send: Should convert and upload as file
            res1 = await send_high_quality_animation(
                bot=mock_bot,
                chat_id=12345678,
                animation_input=test_frames,
                caption="Test Animation First Send",
                filename="test.mp4"
            )
            self.assertIsNotNone(res1)
            self.assertEqual(mock_bot.send_animation.call_count, 1)

            # Verify it cached the file_id in DB
            cached_id = database.get_cached_telegram_media(test_hash, "animation")
            self.assertEqual(cached_id, "BAACAgIA_TEST_DYNAMIC_FILE_ID_9999")

            # 2. Second send with identical media: Should dispatch cached file_id directly (0 CPU conversion!)
            res2 = await send_high_quality_animation(
                bot=mock_bot,
                chat_id=12345678,
                animation_input=test_frames,
                caption="Test Animation Second Send"
            )
            self.assertEqual(mock_bot.send_animation.call_count, 2)
            
            # Check arguments of second call
            last_call = mock_bot.send_animation.call_args
            self.assertEqual(last_call.kwargs.get("animation"), "BAACAgIA_TEST_DYNAMIC_FILE_ID_9999")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
