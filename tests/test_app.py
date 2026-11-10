import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as app_module
from weixin_audio_extractor import MediaResult, ParserRequestError


SAMPLE_PROFILE = {
    "feedInfo": {
        "description": "视频号标题",
        "nickname": "视频号作者",
        "coverUrl": "https://cdn.example.com/cover.jpg",
        "h264VideoInfo": {
            "videoUrl": "https://cdn.example.com/video.mp4",
            "width": 1080,
            "height": 1920,
            "duration": 21,
        },
    }
}


class _FakeYDL:
    seen = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        _FakeYDL.seen.append({"url": url, "download": download, "opts": self.opts})
        return {
            "title": "抖音示例",
            "thumbnail": "https://example.com/a.jpg",
            "duration": 12,
            "uploader": "作者",
            "extractor_key": "Douyin",
            "webpage_url": url,
            "formats": [{"vcodec": "h264", "width": 720, "height": 1280, "ext": "mp4"}],
        }


class AppRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app_module.app.test_client()
        _FakeYDL.seen = []

    def test_weixin_parse_uses_parser_not_ytdlp(self) -> None:
        with mock.patch("app.fetch_video_profile", return_value=SAMPLE_PROFILE) as fetch, \
                mock.patch("app.yt_dlp.YoutubeDL", _FakeYDL):
            response = self.client.post("/api/parse", json={
                "url": "https://weixin.qq.com/sph/AWU0OMKvVm",
            })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["source"], "weixin")
        self.assertEqual(body["source_label"], "微信视频号")
        self.assertEqual(body["title"], "视频号标题")
        self.assertEqual(body["thumbnail"], "https://cdn.example.com/cover.jpg")
        self.assertEqual(body["uploader"], "视频号作者")
        self.assertEqual(body["duration"], 21)
        self.assertEqual(body["max_resolution"], 1080)
        ids = [item["id"] for item in body["qualities"]]
        self.assertEqual(ids, ["best", "mp3", "m4a"])
        fetch.assert_called_once()
        self.assertEqual(_FakeYDL.seen, [])

    def test_weixin_parser_outage_is_chinese(self) -> None:
        with mock.patch(
            "app.fetch_video_profile",
            side_effect=ParserRequestError("Parser request failed: timed out"),
        ):
            response = self.client.post("/api/parse", json={
                "url": "https://weixin.qq.com/sph/AWU0OMKvVm",
            })
        self.assertEqual(response.status_code, 502)
        body = response.get_json()
        self.assertNotIn("sph.litao.workers.dev", body["error"])
        self.assertNotIn("第三方", body["error"])
        self.assertIn("微信视频号", body["error"])
        self.assertIn("Parser request failed", body["detail"])

    def test_article_link_is_not_sent_to_the_parser(self) -> None:
        with mock.patch("app.fetch_video_profile") as fetch:
            response = self.client.post("/api/parse", json={
                "url": "https://mp.weixin.qq.com/s/abc",
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("weixin.qq.com/sph/", response.get_json()["error"])
        fetch.assert_not_called()

    def test_douyin_video_and_short_link_go_to_ytdlp(self) -> None:
        with mock.patch("app.yt_dlp.YoutubeDL", _FakeYDL), \
                mock.patch("app.fetch_video_profile") as fetch:
            video = self.client.post("/api/parse", json={
                "url": "https://www.douyin.com/jingxuan?modal_id=7538955201693994298",
            })
            short = self.client.post("/api/parse", json={
                "url": "https://v.douyin.com/uD7JHhJ-0oo/",
            })
        self.assertEqual(video.status_code, 200)
        self.assertEqual(short.status_code, 200)
        self.assertEqual(video.get_json()["source"], "douyin")
        self.assertEqual(short.get_json()["source"], "douyin")
        self.assertIn("720", [item["id"] for item in video.get_json()["qualities"]])
        self.assertEqual(
            _FakeYDL.seen[0]["url"],
            "https://www.douyin.com/video/7538955201693994298",
        )
        self.assertEqual(_FakeYDL.seen[1]["url"], "https://v.douyin.com/uD7JHhJ-0oo/")
        self.assertFalse(_FakeYDL.seen[0]["download"])
        fetch.assert_not_called()

    def test_youtube_still_uses_ytdlp(self) -> None:
        with mock.patch("app.yt_dlp.YoutubeDL", _FakeYDL), \
                mock.patch("app.fetch_video_profile") as fetch:
            response = self.client.post("/api/parse", json={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source"], "youtube")
        self.assertEqual(
            _FakeYDL.seen[0]["url"],
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        fetch.assert_not_called()

    def test_cookie_file_and_browser_cookies_stay_on_ytdlp_options(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            with mock.patch.object(app_module, "COOKIE_FILE", handle.name), \
                    mock.patch.object(app_module, "COOKIES_FROM_BROWSER", ""):
                opts = app_module.base_opts()
            self.assertEqual(opts["cookiefile"], handle.name)
            self.assertNotIn("cookiesfrombrowser", opts)
        with mock.patch.object(app_module, "COOKIE_FILE", "/no/such/cookies.txt"), \
                mock.patch.object(app_module, "COOKIES_FROM_BROWSER", "chrome"):
            opts = app_module.base_opts()
        self.assertEqual(opts["cookiesfrombrowser"], ("chrome",))

    def test_douyin_cookie_error_mentions_cookies_txt(self) -> None:
        message = app_module.friendly_error(RuntimeError(
            "ERROR: [Douyin] Fresh cookies (not necessarily logged in) are needed"
        ))
        self.assertIn("cookies.txt", message["error"])
        self.assertIn("YTDLP_COOKIES_FROM_BROWSER", message["error"])
        self.assertIn("抖音", message["error"])

    def test_weixin_height_ladder_is_rejected(self) -> None:
        response = self.client.post("/api/download", json={
            "url": "https://weixin.qq.com/sph/AWU0OMKvVm",
            "quality": "720",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("1080p/720p/480p", response.get_json()["error"])

    def test_weixin_download_task_finishes_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "视频号标题 [weixin_AWU0OMKvVm] [best].mp4"
            media.write_bytes(b"mp4")
            result = MediaResult(
                share_url="https://weixin.qq.com/sph/AWU0OMKvVm",
                source_video_url="https://cdn.example.com/video.mp4",
                media_path=media,
                metadata_path=Path(temp_dir) / "weixin_AWU0OMKvVm.json",
                video_path=media,
                title="视频号标题",
                thumbnail="https://cdn.example.com/cover.jpg",
                uploader="视频号作者",
                duration=21,
                width=1080,
                height=1920,
            )
            with mock.patch.object(app_module, "DOWNLOAD_DIR", temp_dir), \
                    mock.patch("app.download_media", return_value=result) as download:
                app_module.TASKS["tid"] = {"id": "tid", "status": "queued"}
                app_module.run_weixin_download(
                    "tid", "https://weixin.qq.com/sph/AWU0OMKvVm", "best", False,
                )
            self.assertEqual(download.call_args.kwargs["quality"], "best")
            task = app_module.TASKS["tid"]
            self.assertEqual(task["status"], "finished")
            self.assertEqual(task["filename"], media.name)
            self.assertEqual(task["title"], "视频号标题")


if __name__ == "__main__":
    unittest.main()
