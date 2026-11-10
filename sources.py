# -*- coding: utf-8 -*-
"""识别视频链接属于哪个来源，并把抖音地址整理成 yt-dlp 官方提取器能接住的形式。

不访问网络。抖音短链 v.douyin.com 保持原样，交给 yt-dlp 的 generic 重定向；
已经带视频号的页面则改写成 DouyinIE 接受的 /video/<id>。
"""
import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}
TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "tiktokv.com",
    "www.tiktokv.com",
}
DOUYIN_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "v.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
}

SOURCE_LABELS = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "douyin": "抖音",
    "weixin": "微信视频号",
    "weixin_other": "微信",
    "generic": "其他",
}

WEIXIN_SHARE_HINT = (
    "这不是微信视频号分享链接。请粘贴 weixin.qq.com/sph/... "
    "（或 channels.weixin.qq.com 上的视频号页面），而不是公众号文章。"
)

_VIDEO_PATH_ID = re.compile(r"/(?:share/)?video/(\d+)")


def hostname(url):
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def detect_source(url):
    """返回 youtube / tiktok / douyin / weixin / weixin_other / generic。"""
    if not isinstance(url, str):
        return "generic"
    host = hostname(url)
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return "youtube"
    if host in TIKTOK_HOSTS or host.endswith(".tiktok.com"):
        return "tiktok"
    if host in DOUYIN_HOSTS or host.endswith(".douyin.com") or host.endswith(".iesdouyin.com"):
        return "douyin"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "generic"
    path = parsed.path or ""
    if host == "weixin.qq.com" and (path == "/sph" or path.startswith("/sph/")):
        return "weixin"
    if host == "channels.weixin.qq.com":
        return "weixin"
    if host == "weixin.qq.com" or host.endswith(".weixin.qq.com"):
        return "weixin_other"
    return "generic"


def canonical_douyin_url(url):
    """把能看出视频号的抖音链接改成 DouyinIE 的 /video/<id>。

    v.douyin.com 短链本身没有视频号，原样返回，由 yt-dlp 跟随官方重定向。
    """
    if not isinstance(url, str):
        return url
    host = hostname(url)
    if host == "v.douyin.com":
        return url
    if host not in DOUYIN_HOSTS and not host.endswith(".douyin.com") and not host.endswith(".iesdouyin.com"):
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    path = parsed.path or ""
    # /share/video/<id> 也含有 /video/<id>，不能当成官方视频页原样留下。
    if host in {"douyin.com", "www.douyin.com"} and re.fullmatch(r"/video/\d+/?", path):
        return url
    match = _VIDEO_PATH_ID.search(path)
    video_id = match.group(1) if match else ""
    if not video_id:
        modal = parse_qs(parsed.query).get("modal_id", [""])[0]
        if modal.isdigit():
            video_id = modal
    if video_id:
        return f"https://www.douyin.com/video/{video_id}"
    return url
