import unittest

from yt_dlp.extractor.generic import GenericIE
from yt_dlp.extractor.tiktok import DouyinIE

from sources import canonical_douyin_url, detect_source


class DetectSourceTests(unittest.TestCase):
    def test_routes_known_hosts(self) -> None:
        cases = [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
            ("https://youtu.be/dQw4w9WgXcQ", "youtube"),
            ("https://music.youtube.com/watch?v=abc", "youtube"),
            ("https://www.youtube-nocookie.com/embed/abc", "youtube"),
            ("https://www.tiktok.com/@user/video/1234567890", "tiktok"),
            ("https://vm.tiktok.com/ZTR45GpSF/", "tiktok"),
            ("https://vt.tiktok.com/ZSe4FqkKd/", "tiktok"),
            ("https://www.douyin.com/video/6961737553342991651", "douyin"),
            ("https://douyin.com/video/1", "douyin"),
            ("https://v.douyin.com/uD7JHhJ-0oo/", "douyin"),
            ("https://www.douyin.com/jingxuan?modal_id=7538955201693994298", "douyin"),
            ("https://www.iesdouyin.com/share/video/6961737553342991651/", "douyin"),
            ("https://weixin.qq.com/sph/AWU0OMKvVm", "weixin"),
            ("https://weixin.qq.com/sph/AWU0OMKvVm/", "weixin"),
            ("https://channels.weixin.qq.com/finder-preview/pages/sph?id=AoPX5bEBDd", "weixin"),
            ("https://mp.weixin.qq.com/s/abc", "weixin_other"),
            ("https://example.com/video/1", "generic"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(detect_source(url), expected)

    def test_does_not_confuse_tiktok_with_douyin(self) -> None:
        self.assertEqual(detect_source("https://www.tiktok.com/@douyin/video/1"), "tiktok")
        self.assertNotEqual(detect_source("https://v.douyin.com/abc/"), "tiktok")


class CanonicalDouyinUrlTests(unittest.TestCase):
    def test_short_links_stay_for_ytdlp_redirect(self) -> None:
        url = "https://v.douyin.com/uD7JHhJ-0oo/"
        self.assertEqual(canonical_douyin_url(url), url)
        self.assertFalse(DouyinIE.suitable(url))
        self.assertTrue(GenericIE.suitable(url))

    def test_video_path_is_left_to_the_official_extractor(self) -> None:
        url = "https://www.douyin.com/video/6961737553342991651?previous_page=web_code_link"
        self.assertEqual(canonical_douyin_url(url), url)
        self.assertTrue(DouyinIE.suitable(url))

    def test_modal_id_and_share_paths_become_video_urls(self) -> None:
        cases = {
            "https://www.douyin.com/jingxuan?modal_id=7538955201693994298":
                "https://www.douyin.com/video/7538955201693994298",
            "https://www.douyin.com/share/video/123":
                "https://www.douyin.com/video/123",
            "https://www.iesdouyin.com/share/video/6961737553342991651/":
                "https://www.douyin.com/video/6961737553342991651",
        }
        for raw, expected in cases.items():
            with self.subTest(url=raw):
                canonical = canonical_douyin_url(raw)
                self.assertEqual(canonical, expected)
                self.assertTrue(DouyinIE.suitable(canonical))

    def test_other_sites_are_unchanged(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(canonical_douyin_url(url), url)


if __name__ == "__main__":
    unittest.main()
