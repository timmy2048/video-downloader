import io
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from weixin_audio_extractor.core import (  # noqa: E402
    DEFAULT_PARSER_ENDPOINT,
    DownloadError,
    ExtractionError,
    ParserRequestError,
    ParserResponseError,
    build_media_filename,
    build_output_stem,
    download_media,
    extract_weixin_audio,
    fetch_video_profile,
    select_video_url,
    summarize_profile,
    user_message,
    _extract_audio_with_ffmpeg,
)


PROFILE = {
    "feedInfo": {
        "description": "示例<title>标题</title>",
        "nickname": "示例作者",
        "coverUrl": "https://cdn.example.com/cover.jpg",
        "h264VideoInfo": {
            "videoUrl": "https://cdn.example.com/video-h264.mp4",
            "width": 720,
            "height": 1280,
            "duration": 15,
            "coverUrl": "https://cdn.example.com/h264-cover.jpg",
        },
        "h265VideoInfo": {"videoUrl": "https://cdn.example.com/video-h265.mp4"},
    }
}


class SelectVideoUrlTests(unittest.TestCase):
    def test_select_video_url_prefers_h264(self) -> None:
        self.assertEqual(
            select_video_url(PROFILE),
            "https://cdn.example.com/video-h264.mp4",
        )

    def test_select_video_url_falls_back_to_h265(self) -> None:
        payload = {
            "feedInfo": {
                "h264VideoInfo": {},
                "h265VideoInfo": {"videoUrl": "https://cdn.example.com/video-h265.mp4"},
            }
        }

        self.assertEqual(
            select_video_url(payload),
            "https://cdn.example.com/video-h265.mp4",
        )

    def test_select_video_url_supports_nested_data_feed_info(self) -> None:
        payload = {
            "data": {
                "feedInfo": {
                    "h264VideoInfo": {
                        "videoUrl": "https://cdn.example.com/video-h264.mp4"
                    },
                    "h265VideoInfo": {
                        "videoUrl": "https://cdn.example.com/video-h265.mp4"
                    },
                }
            }
        }

        self.assertEqual(
            select_video_url(payload),
            "https://cdn.example.com/video-h264.mp4",
        )

    def test_select_video_url_falls_back_to_feed_video_url(self) -> None:
        payload = {
            "feedInfo": {
                "h264VideoInfo": {"videoUrl": "javascript:alert(1)"},
                "h265VideoInfo": {},
                "videoUrl": "https://cdn.example.com/plain.mp4",
            }
        }
        self.assertEqual(select_video_url(payload), "https://cdn.example.com/plain.mp4")

    def test_select_video_url_raises_when_missing(self) -> None:
        payload = {
            "feedInfo": {
                "h264VideoInfo": {},
                "h265VideoInfo": {},
            }
        }

        with self.assertRaises(ParserResponseError):
            select_video_url(payload)


class SummarizeProfileTests(unittest.TestCase):
    def test_summarize_profile_reads_title_cover_and_short_side_inputs(self) -> None:
        summary = summarize_profile(PROFILE)
        self.assertEqual(summary["title"], "示例 标题")
        self.assertEqual(summary["thumbnail"], "https://cdn.example.com/cover.jpg")
        self.assertEqual(summary["uploader"], "示例作者")
        self.assertEqual(summary["duration"], 15)
        self.assertEqual(summary["width"], 720)
        self.assertEqual(summary["height"], 1280)

    def test_summarize_profile_converts_duration_ms(self) -> None:
        summary = summarize_profile({
            "feedInfo": {"h264VideoInfo": {"durationMs": 12500}},
        })
        self.assertEqual(summary["duration"], 12)

    def test_build_media_filename_strips_path_characters(self) -> None:
        profile = {"feedInfo": {"description": "../秘密:视频"}}
        self.assertEqual(
            build_media_filename("https://weixin.qq.com/sph/AWU0OMKvVm", profile, "best"),
            "秘密 视频 [weixin_AWU0OMKvVm] [best].mp4",
        )
        self.assertNotIn("/", build_media_filename(
            "https://weixin.qq.com/sph/AWU0OMKvVm", profile, "mp3",
        ))


class BuildOutputStemTests(unittest.TestCase):
    def test_build_output_stem_uses_share_slug(self) -> None:
        self.assertEqual(
            build_output_stem("https://weixin.qq.com/sph/AWU0OMKvVm"),
            "weixin_AWU0OMKvVm",
        )

    def test_build_output_stem_uses_query_id_when_path_is_only_sph(self) -> None:
        self.assertEqual(
            build_output_stem(
                "https://channels.weixin.qq.com/finder-preview/pages/sph?id=AoPX5bEBDd"
            ),
            "weixin_AoPX5bEBDd",
        )

    def test_build_output_stem_hashes_when_path_has_no_slug(self) -> None:
        stem = build_output_stem("https://weixin.qq.com/")

        self.assertRegex(stem, r"^weixin_[0-9a-f]{10}$")


class FailureHandlingTests(unittest.TestCase):
    def test_fetch_video_profile_raises_on_http_error(self) -> None:
        http_error = HTTPError(
            url="https://parser.example.com",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=io.BytesIO(b"temporarily unavailable"),
        )
        self.addCleanup(http_error.close)
        with mock.patch(
            "weixin_audio_extractor.core.request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaisesRegex(Exception, "HTTP 503"):
                fetch_video_profile("https://weixin.qq.com/sph/AWU0OMKvVm")

    def test_parser_outage_message_names_the_third_party_endpoint(self) -> None:
        message = user_message(ParserRequestError("Parser request failed: timed out"))
        self.assertIn(DEFAULT_PARSER_ENDPOINT, message)
        self.assertIn("第三方解析服务", message)
        self.assertIn("宕机", message)

    def test_unusable_payload_message_is_chinese(self) -> None:
        message = user_message(ParserResponseError(
            "Parser response did not contain an H.264 or H.265 video URL."
        ))
        self.assertIn("没有返回可用的视频地址", message)
        self.assertIn(DEFAULT_PARSER_ENDPOINT, message)

    def test_extract_weixin_audio_rejects_invalid_share_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(Exception, "absolute http"):
                extract_weixin_audio("not-a-url", Path(temp_dir))

    def test_extract_weixin_audio_cleans_up_video_on_ffmpeg_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fake_download(_: str, destination: Path, *, timeout: int, **kwargs) -> None:
                del timeout, kwargs
                destination.write_bytes(b"video-bytes")

            with mock.patch(
                "weixin_audio_extractor.core.fetch_video_profile",
                return_value={
                    "feedInfo": {
                        "h264VideoInfo": {"videoUrl": "https://cdn.example.com/video.mp4"}
                    }
                },
            ), mock.patch(
                "weixin_audio_extractor.core._download_file",
                side_effect=fake_download,
            ), mock.patch(
                "weixin_audio_extractor.core._extract_audio_with_ffmpeg",
                side_effect=ExtractionError("ffmpeg failed"),
            ):
                with self.assertRaises(ExtractionError):
                    extract_weixin_audio(
                        "https://weixin.qq.com/sph/AWU0OMKvVm",
                        output_dir,
                    )

            self.assertFalse((output_dir / "weixin_AWU0OMKvVm.mp4").exists())


class DownloadMediaTests(unittest.TestCase):
    def test_audio_download_deletes_mp4_unless_keep_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fake_download(_: str, destination: Path, *, timeout: int, **kwargs) -> None:
                del timeout, kwargs
                destination.write_bytes(b"video-bytes")

            def fake_ffmpeg(video_path: Path, audio_path: Path, codec: str = "mp3") -> None:
                self.assertEqual(codec, "mp3")
                self.assertTrue(video_path.exists())
                audio_path.write_bytes(b"audio")

            with mock.patch(
                "weixin_audio_extractor.core.fetch_video_profile",
                return_value=PROFILE,
            ), mock.patch(
                "weixin_audio_extractor.core._download_file",
                side_effect=fake_download,
            ), mock.patch(
                "weixin_audio_extractor.core._extract_audio_with_ffmpeg",
                side_effect=fake_ffmpeg,
            ):
                result = download_media(
                    "https://weixin.qq.com/sph/AWU0OMKvVm",
                    output_dir,
                    quality="mp3",
                    keep_video=False,
                )

            self.assertTrue(result.media_path.exists())
            self.assertEqual(result.media_path.suffix, ".mp3")
            self.assertIsNone(result.video_path)
            self.assertFalse(any(path.suffix == ".mp4" for path in output_dir.iterdir()))
            self.assertTrue((output_dir / "weixin_AWU0OMKvVm.json").is_file())
            saved = (output_dir / "weixin_AWU0OMKvVm.json").read_text(encoding="utf-8")
            self.assertIn("video-h264.mp4", saved)

    def test_keep_video_leaves_the_mp4(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            def fake_download(_: str, destination: Path, *, timeout: int, **kwargs) -> None:
                del timeout, kwargs
                destination.write_bytes(b"video-bytes")

            def fake_ffmpeg(video_path: Path, audio_path: Path, codec: str = "mp3") -> None:
                del video_path, codec
                audio_path.write_bytes(b"audio")

            with mock.patch(
                "weixin_audio_extractor.core.fetch_video_profile",
                return_value=PROFILE,
            ), mock.patch(
                "weixin_audio_extractor.core._download_file",
                side_effect=fake_download,
            ), mock.patch(
                "weixin_audio_extractor.core._extract_audio_with_ffmpeg",
                side_effect=fake_ffmpeg,
            ):
                result = download_media(
                    "https://weixin.qq.com/sph/AWU0OMKvVm",
                    output_dir,
                    quality="m4a",
                    keep_video=True,
                )

            self.assertEqual(result.media_path.suffix, ".m4a")
            self.assertIsNotNone(result.video_path)
            self.assertTrue(result.video_path.is_file())

    def test_extract_audio_uses_system_ffmpeg(self) -> None:
        completed = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch(
            "weixin_audio_extractor.core.shutil.which", return_value="/usr/bin/ffmpeg",
        ) as which, mock.patch(
            "weixin_audio_extractor.core.subprocess.run", return_value=completed,
        ) as run:
            _extract_audio_with_ffmpeg(Path("in.mp4"), Path("out.mp3"))
            _extract_audio_with_ffmpeg(Path("in.mp4"), Path("out.m4a"), codec="m4a")
        which.assert_called_with("ffmpeg")
        mp3_cmd = run.call_args_list[0].args[0]
        m4a_cmd = run.call_args_list[1].args[0]
        self.assertEqual(mp3_cmd[0], "/usr/bin/ffmpeg")
        self.assertIn("libmp3lame", mp3_cmd)
        self.assertIn("aac", m4a_cmd)
        self.assertNotIn("imageio_ffmpeg", sys.modules)

    def test_download_failure_removes_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with mock.patch(
                "weixin_audio_extractor.core.fetch_video_profile",
                return_value=PROFILE,
            ), mock.patch(
                "weixin_audio_extractor.core._download_file",
                side_effect=DownloadError("nope"),
            ):
                with self.assertRaises(DownloadError):
                    download_media(
                        "https://weixin.qq.com/sph/AWU0OMKvVm",
                        output_dir,
                        quality="best",
                    )
            self.assertEqual(list(output_dir.iterdir()), [])

    def test_missing_ffmpeg_raises(self) -> None:
        with mock.patch("weixin_audio_extractor.core.shutil.which", return_value=None):
            with self.assertRaises(ExtractionError):
                _extract_audio_with_ffmpeg(Path("in.mp4"), Path("out.mp3"))


if __name__ == "__main__":
    unittest.main()
