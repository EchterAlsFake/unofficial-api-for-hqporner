import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from hqporner_api.api import create_parser, run_main


class TestCLI(unittest.IsolatedAsyncioTestCase):
    def test_parser_download(self):
        parser = create_parser()
        args = parser.parse_args(["--download", "https://hqporner.com/hd/123", "--output", "/tmp/out"])
        self.assertEqual(args.download, "https://hqporner.com/hd/123")
        self.assertEqual(args.output, "/tmp/out")
        self.assertEqual(args.quality, "best")
        self.assertEqual(args.no_title, "False")

    def test_parser_search(self):
        parser = create_parser()
        args = parser.parse_args(["--search", "brunette", "--output", "/tmp/out", "--pages", "2"])
        self.assertEqual(args.search, "brunette")
        self.assertEqual(args.pages, 2)

    async def test_run_main_download(self):
        mock_video = MagicMock()
        mock_video.title = "Sample Video"
        mock_video.length = "10:00"
        mock_video.url = "https://hqporner.com/hd/123"
        mock_video.load_fields = AsyncMock()
        mock_video.download = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_video = AsyncMock(return_value=mock_video)

        with patch("hqporner_api.api.Client", return_value=mock_client):
            await run_main(["--download", "https://hqporner.com/hd/123", "--output", "/tmp/out"])

        mock_client.get_video.assert_awaited_once_with("https://hqporner.com/hd/123")
        mock_video.download.assert_awaited_once()

    async def test_run_main_file(self):
        mock_video = MagicMock()
        mock_video.title = "Sample Video"
        mock_video.length = "10:00"
        mock_video.url = "https://hqporner.com/hd/123"
        mock_video.load_fields = AsyncMock()
        mock_video.download = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_video = AsyncMock(return_value=mock_video)

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("https://hqporner.com/hd/1\nhttps://hqporner.com/hd/2\n")
            f.flush()
            temp_name = f.name

        try:
            with patch("hqporner_api.api.Client", return_value=mock_client):
                await run_main(["--file", temp_name, "--output", "/tmp/out"])

            self.assertEqual(mock_client.get_video.await_count, 2)
            self.assertEqual(mock_video.download.await_count, 2)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

    async def test_real_download(self):
        """Integration test: verifies real video download via CLI."""
        url = "https://hqporner.com/hdporn/126829-this_is_our_story.html"
        with tempfile.TemporaryDirectory() as tmp_dir:
            await run_main(["--download", url, "--output", tmp_dir, "--quality", "worst"])
            files = [f for f in os.listdir(tmp_dir) if not f.endswith(".tmp")]
            self.assertTrue(len(files) > 0, "Expected downloaded video file in output directory")


if __name__ == "__main__":
    unittest.main()
