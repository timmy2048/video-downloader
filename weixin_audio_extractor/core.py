# Ported from https://github.com/timmy2048/weixin-audio-extractor
# Copyright (c) 2026 timmy2048
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""微信视频号分享链接的可复用核心。

分享链接在本机直接交给微信自己的预览接口
https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info ：
weixin.qq.com/sph/<id> 和 finder-preview/pages/sph?id=<id> 用 shortUri，
已经带 token 与 eid 的预览页用 generalToken + exportId。
优先用 feedInfo.h264VideoInfo.videoUrl，否则 h265，再否则 feedInfo.videoUrl，
下载 MP4，并可用本机 ffmpeg 抽出 MP3/M4A。

公开短链的预览经常只有标题、作者和封面。若本机配置了自己的元宝 Cookie
（环境变量 YUANBAO_COOKIE，或项目目录 yuanbao_cookie.txt），会再用它向
yuanbao.tencent.com 换取预览 token，然后走同一个微信接口。这里不内置密钥，
也不再调用 sph.litao.workers.dev。音频转换使用系统 PATH 里的 ffmpeg。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
from typing import Any, Callable
from urllib import error, parse, request


FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
CHANNELS_REFERER = "https://channels.weixin.qq.com/"
DEFAULT_TIMEOUT_SECONDS = 30
_SHORT_URI_RE = re.compile(r"^[A-Za-z0-9]+")
_SPH_PATH_RE = re.compile(r"/sph/([A-Za-z0-9_-]+)")
_HEADER_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")
VIDEO_SUFFIX = ".mp4"
AUDIO_SUFFIX = ".mp3"
METADATA_SUFFIX = ".json"
_SLUG_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")
_FILENAME_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)
ProgressCallback = Callable[[int, int | None], None]
StageCallback = Callable[[str], None]


class WeixinAudioExtractorError(RuntimeError):
    """Base exception for user-facing extraction failures."""


class ParserRequestError(WeixinAudioExtractorError):
    """Raised when the parser endpoint cannot be reached successfully."""


class ParserResponseError(WeixinAudioExtractorError):
    """Raised when the parser endpoint returns an unusable payload."""


class DownloadError(WeixinAudioExtractorError):
    """Raised when the resolved video cannot be downloaded."""


class ExtractionError(WeixinAudioExtractorError):
    """Raised when ffmpeg fails to extract audio."""


@dataclass(frozen=True)
class ExtractionResult:
    share_url: str
    source_video_url: str
    audio_path: Path
    metadata_path: Path
    video_path: Path | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "share_url": self.share_url,
            "source_video_url": self.source_video_url,
            "audio_path": str(self.audio_path),
            "metadata_path": str(self.metadata_path),
            "video_path": str(self.video_path) if self.video_path is not None else None,
        }


@dataclass(frozen=True)
class ShareTarget:
    """本机向微信预览接口查询时用的参数。"""

    short_uri: str
    export_id: str
    token: str
    exchange_url: str


@dataclass(frozen=True)
class MediaResult:
    share_url: str
    source_video_url: str
    media_path: Path
    metadata_path: Path
    video_path: Path | None
    title: str | None
    thumbnail: str | None
    uploader: str | None
    duration: int | None
    width: int | None
    height: int | None


def parse_share_target(share_url: str) -> ShareTarget:
    """从分享链接取出 shortUri，或预览页上的 exportId 与 token。"""
    parsed_url = parse.urlparse(share_url.strip())
    host = (parsed_url.hostname or "").lower().rstrip(".")
    if parsed_url.scheme not in {"http", "https"} or not host:
        raise WeixinAudioExtractorError(
            "Share URL must be an absolute http(s) URL."
        )
    query = parse.parse_qs(parsed_url.query)
    token = _first_query(query, "token")
    export_id = _first_query(query, "eid") or _first_query(query, "exportId")
    path = parsed_url.path or ""
    path_match = _SPH_PATH_RE.search(path)
    path_id = path_match.group(1) if path_match else ""
    short_uri = ""
    channels_host = host == "channels.weixin.qq.com" or host.endswith(".channels.weixin.qq.com")
    if host == "weixin.qq.com" and path_id:
        short_uri = _short_uri(path_id)
    elif channels_host and ("/sph" in path or _first_query(query, "id")) and not export_id:
        short_uri = _short_uri(_first_query(query, "id") or path_id)
    elif channels_host and path_id and not export_id:
        short_uri = _short_uri(path_id)
    if not short_uri and not export_id:
        raise WeixinAudioExtractorError("not a channels share link")
    exchange_url = f"https://weixin.qq.com/sph/{short_uri}" if short_uri else share_url.strip()
    return ShareTarget(
        short_uri=short_uri,
        export_id=export_id.strip(),
        token=token.strip(),
        exchange_url=exchange_url,
    )


def fetch_video_profile(
    share_url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """在本机解析分享链接，返回带 feedInfo 的资料。没有可播放地址时抛错。"""
    target = parse_share_target(share_url)
    profile = _request_feed_info(target, timeout=timeout)
    if _profile_has_video(profile):
        return profile

    cookie = _load_yuanbao_cookie()
    if cookie:
        exchanged = _exchange_with_yuanbao(target.exchange_url, cookie, timeout=timeout)
        profile = _request_feed_info(exchanged, timeout=timeout)
        if _profile_has_video(profile):
            return profile

    if _is_picture_post(profile):
        raise ParserResponseError("这条分享是图集，不是视频。")
    blocked = _feed_block_message(profile)
    if blocked:
        raise ParserResponseError(blocked)
    title = summarize_profile(profile).get("title") or ""
    detail = "Parser response did not contain an H.264 or H.265 video URL."
    if title:
        detail = f"{detail} title={title}"
    raise ParserResponseError(detail)


def select_video_url(profile: dict[str, Any]) -> str:
    feed_info = _extract_feed_info(profile)
    if not isinstance(feed_info, dict):
        raise ParserResponseError("Parser response did not contain feedInfo.")

    for key in ("h264VideoInfo", "h265VideoInfo"):
        candidate = feed_info.get(key)
        if not isinstance(candidate, dict):
            continue
        video_url = _http_url(candidate.get("videoUrl"))
        if video_url:
            return video_url

    direct = _http_url(feed_info.get("videoUrl"))
    if direct:
        return direct

    raise ParserResponseError(
        "Parser response did not contain an H.264 or H.265 video URL."
    )


def summarize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """从解析结果里取出网页可以展示的标题、封面、作者和分辨率。缺了就留空。"""
    feed = _extract_feed_info(profile) or {}
    h264 = feed.get("h264VideoInfo") if isinstance(feed.get("h264VideoInfo"), dict) else {}
    h265 = feed.get("h265VideoInfo") if isinstance(feed.get("h265VideoInfo"), dict) else {}
    contact = feed.get("contact") if isinstance(feed.get("contact"), dict) else {}
    author = _extract_author_info(profile)
    title = _plain(
        feed.get("description")
        or feed.get("desc")
        or feed.get("title")
    )
    thumbnail = _http_url(
        feed.get("coverUrl")
        or feed.get("cover")
        or feed.get("cover_url")
        or h264.get("coverUrl")
        or h265.get("coverUrl")
    )
    uploader = _plain(
        feed.get("nickname")
        or feed.get("author")
        or contact.get("nickname")
        or author.get("nickname")
    )
    duration = _positive_int(h264.get("duration") or h265.get("duration") or feed.get("duration"))
    if duration is None:
        duration_ms = _positive_int(
            h264.get("durationMs") or h265.get("durationMs") or feed.get("durationMs")
        )
        if duration_ms is not None and duration_ms >= 1000:
            duration = duration_ms // 1000
    width = _positive_int(h264.get("width") or h265.get("width") or feed.get("width"))
    height = _positive_int(h264.get("height") or h265.get("height") or feed.get("height"))
    return {
        "title": title,
        "thumbnail": thumbnail,
        "uploader": uploader,
        "duration": duration,
        "width": width,
        "height": height,
    }


def build_output_stem(share_url: str) -> str:
    parsed_url = parse.urlparse(share_url)
    segments = [segment for segment in parsed_url.path.split("/") if segment]
    query_id = parse.parse_qs(parsed_url.query).get("id", [""])[0]
    slug_source = ""
    if segments:
        last = parse.unquote(segments[-1])
        # channels.weixin.qq.com/.../sph?id=... 的路径末段都是 sph，用查询参数区分文件。
        if last.lower() == "sph" and query_id:
            slug_source = query_id
        else:
            slug_source = last
    elif query_id:
        slug_source = query_id
    slug = _sanitize_slug(slug_source) if slug_source else ""

    if not slug:
        slug = hashlib.sha1(share_url.encode("utf-8")).hexdigest()[:10]

    return f"weixin_{slug}"


def build_media_filename(share_url: str, profile: dict[str, Any], quality: str) -> str:
    if quality not in {"best", "mp3", "m4a"}:
        raise ValueError("未知的清晰度选项")
    summary = summarize_profile(profile)
    stem = build_output_stem(share_url)
    title = _safe_filename(summary.get("title") or "", limit=80)
    base = f"{title} [{stem}]" if title else stem
    tag = " [best]" if quality == "best" else ""
    ext = "mp4" if quality == "best" else quality
    return f"{base}{tag}.{ext}"


def user_message(exc: BaseException) -> str:
    """把核心异常翻成页面和命令行用的中文说明。"""
    if isinstance(exc, ParserRequestError):
        text = str(exc)
        if "YUANBAO_COOKIE" in text:
            return "本机的元宝 Cookie 格式不正确。请粘贴一整行 Cookie，不要换行，也不要发给别人。"
        if "401" in text or "403" in text:
            return (
                "本机配置的元宝登录已被拒绝。"
                "请重新登录 yuanbao.tencent.com，更新环境变量 YUANBAO_COOKIE"
                "或项目目录里的 yuanbao_cookie.txt 后再试。"
                "Cookie 等同登录凭证，不要发给别人，也不要做成公开服务。"
            )
        return "连接微信视频号失败（网络超时或请求被拒绝）。请检查本机网络后重试。"
    if isinstance(exc, ParserResponseError):
        text = str(exc)
        if "图集" in text:
            return "这条微信视频号分享是图集，不是视频。当前只能下载视频。"
        if "feedInfo" in text or "H.264" in text or "H.265" in text:
            recognized = ""
            marker = "title="
            if marker in text:
                recognized = text.split(marker, 1)[1].strip()
            lead = f"已识别「{recognized}」，但" if recognized else ""
            return (
                f"{lead}微信视频号的公开预览没有返回可播放的视频地址。"
                "链接可能已失效，或这条内容需要在微信里登录后才能观看。"
                "带 token 和 eid 的 channels.weixin.qq.com 预览链接请整段粘贴。"
                "普通 weixin.qq.com/sph/ 短链也可以在本机配置你自己的元宝 Cookie"
                "（环境变量 YUANBAO_COOKIE，或项目目录的 yuanbao_cookie.txt）后再解析。"
                "不要把 Cookie 发给别人。"
            )
        if text and "Parser response" not in text:
            return f"微信视频号暂时无法播放这条内容。{text}"
        return "微信视频号返回了无法识别的内容。请确认这是 weixin.qq.com/sph/ 开头的视频号分享链接。"
    if isinstance(exc, DownloadError):
        return "已经拿到视频地址，但下载 MP4 失败。地址可能已过期，或网络中断。请重新解析后再试。"
    if isinstance(exc, ExtractionError):
        low = str(exc).lower()
        if "not found" in low or "ffmpeg" in low:
            return "提取音频需要本机的 ffmpeg。请安装 ffmpeg 后重启程序，或改为下载视频 MP4。"
        return "ffmpeg 提取音频失败。可以改为下载视频 MP4，或检查 ffmpeg 能否处理该文件。"
    if isinstance(exc, WeixinAudioExtractorError):
        if "absolute http" in str(exc).lower():
            return "请输入以 http:// 或 https:// 开头的微信视频号分享链接。"
        return "处理微信视频号链接失败。请确认链接是 weixin.qq.com/sph/ 形式的分享地址。"
    return "处理微信视频号链接失败。"


def extract_weixin_audio(
    share_url: str,
    output_dir: Path,
    *,
    keep_video: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ExtractionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = build_output_stem(share_url)
    audio_path = output_dir / f"{stem}{AUDIO_SUFFIX}"
    metadata_path = output_dir / f"{stem}{METADATA_SUFFIX}"
    video_path = output_dir / f"{stem}{VIDEO_SUFFIX}"

    profile = fetch_video_profile(share_url, timeout=timeout)
    source_video_url = select_video_url(profile)

    _write_metadata(profile, metadata_path)
    _download_file(source_video_url, video_path, timeout=timeout, referer=CHANNELS_REFERER)

    try:
        _extract_audio_with_ffmpeg(video_path, audio_path)
    finally:
        if not keep_video and video_path.exists():
            video_path.unlink()

    return ExtractionResult(
        share_url=share_url,
        source_video_url=source_video_url,
        audio_path=audio_path,
        metadata_path=metadata_path,
        video_path=video_path if keep_video else None,
    )


def download_media(
    share_url: str,
    output_dir: Path,
    *,
    quality: str = "best",
    keep_video: bool = False,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    on_progress: ProgressCallback | None = None,
    on_stage: StageCallback | None = None,
) -> MediaResult:
    """给网页用的下载：best 保留 MP4，mp3/m4a 抽音频，可选保留 MP4。"""
    if quality not in {"best", "mp3", "m4a"}:
        raise ValueError("未知的清晰度选项")
    output_dir.mkdir(parents=True, exist_ok=True)
    if on_stage:
        on_stage("parsing")
    profile = fetch_video_profile(share_url, timeout=timeout)
    source_video_url = select_video_url(profile)
    summary = summarize_profile(profile)
    stem = build_output_stem(share_url)
    metadata_path = output_dir / f"{stem}{METADATA_SUFFIX}"
    video_final = output_dir / build_media_filename(share_url, profile, "best")
    _write_metadata(profile, metadata_path)

    if on_stage:
        on_stage("downloading")
    partial = Path(str(video_final) + ".part")
    try:
        _download_file(
            source_video_url,
            partial,
            timeout=timeout,
            progress=on_progress,
            referer=CHANNELS_REFERER,
        )
        partial.replace(video_final)
    except Exception:
        if partial.exists():
            partial.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        raise

    media_path = video_final
    kept_video: Path | None = video_final
    if quality != "best":
        media_path = output_dir / build_media_filename(share_url, profile, quality)
        if on_stage:
            on_stage("extracting")
        try:
            _extract_audio_with_ffmpeg(video_final, media_path, codec=quality)
        finally:
            if not keep_video and video_final.exists():
                video_final.unlink()
                kept_video = None
        if keep_video:
            kept_video = video_final

    return MediaResult(
        share_url=share_url,
        source_video_url=source_video_url,
        media_path=media_path,
        metadata_path=metadata_path,
        video_path=kept_video,
        title=summary.get("title"),
        thumbnail=summary.get("thumbnail"),
        uploader=summary.get("uploader"),
        duration=summary.get("duration"),
        width=summary.get("width"),
        height=summary.get("height"),
    )


def _sanitize_slug(raw_slug: str) -> str:
    sanitized = _SLUG_SANITIZER.sub("-", raw_slug).strip("-._")
    return sanitized[:64]


def _safe_filename(text: str, limit: int = 80) -> str:
    cleaned = _FILENAME_INVALID.sub(" ", text)
    cleaned = _SPACE_RE.sub(" ", cleaned).strip(" .")
    return cleaned[:limit].strip(" .")


def _plain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = _BR_RE.sub(" ", value)
    text = _TAG_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:300] if text else None


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parsed = parse.urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def _extract_feed_info(profile: dict[str, Any]) -> dict[str, Any] | None:
    direct_feed_info = profile.get("feedInfo")
    if isinstance(direct_feed_info, dict):
        return direct_feed_info

    data = profile.get("data")
    if not isinstance(data, dict):
        return None

    nested_feed_info = data.get("feedInfo")
    if isinstance(nested_feed_info, dict):
        return nested_feed_info

    return None


def _extract_author_info(profile: dict[str, Any]) -> dict[str, Any]:
    containers: list[Any] = [profile]
    data = profile.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        author = container.get("authorInfo") if isinstance(container, dict) else None
        if isinstance(author, dict):
            return author
    return {}


def _profile_has_video(profile: dict[str, Any]) -> bool:
    try:
        select_video_url(profile)
    except ParserResponseError:
        return False
    return True


def _is_picture_post(profile: dict[str, Any]) -> bool:
    feed = _extract_feed_info(profile) or {}
    pictures = feed.get("picInfo")
    if not isinstance(pictures, list) or not pictures:
        return False
    return not _profile_has_video(profile)


def _feed_block_message(profile: dict[str, Any]) -> str | None:
    data = profile.get("data") if isinstance(profile.get("data"), dict) else {}
    detail = data.get("errMsg") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        kind = detail.get("type")
        title = _plain(detail.get("title"))
        content = _plain(detail.get("content"))
        blocked = kind not in (None, 0, "0") and (title or content)
        if blocked:
            return "：".join(part for part in (title, content) if part)
    err_code = profile.get("errCode")
    if isinstance(err_code, int) and err_code != 0:
        return _plain(profile.get("errMsg")) or f"errCode {err_code}"
    error_body = profile.get("error")
    if isinstance(error_body, dict):
        return _plain(error_body.get("message")) or "微信视频号预览接口拒绝了这次请求。"
    return None


def _first_query(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if not values or not isinstance(values[0], str):
        return ""
    return values[0].strip()


def _short_uri(raw: str) -> str:
    match = _SHORT_URI_RE.match(raw.strip())
    return match.group(0) if match else ""


def _load_yuanbao_cookie() -> str:
    """读取本机自愿提供的元宝 Cookie。没有就返回空字符串，绝不内置密钥。"""
    env_value = os.environ.get("YUANBAO_COOKIE", "").strip()
    text = env_value
    if not text:
        file_env = os.environ.get("YUANBAO_COOKIE_FILE", "").strip()
        candidates = []
        if file_env:
            candidates.append(Path(file_env).expanduser())
        root = Path(__file__).resolve().parents[1]
        candidates.append(root / "yuanbao_cookie.txt")
        candidates.append(Path.cwd() / "yuanbao_cookie.txt")
        seen: set[Path] = set()
        for path in candidates:
            resolved = path
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                if resolved.is_file():
                    text = resolved.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                break
    if not text:
        return ""
    if _HEADER_UNSAFE_RE.search(text):
        raise ParserRequestError("YUANBAO_COOKIE is not a single header value.")
    return text


def _request_feed_info(target: ShareTarget, *, timeout: int) -> dict[str, Any]:
    body: dict[str, Any] = {"baseReq": {"generalToken": target.token}}
    if target.export_id and not target.short_uri:
        body["exportId"] = target.export_id
    elif target.short_uri:
        body["shortUri"] = target.short_uri
    else:
        body["exportId"] = target.export_id
    referer = CHANNELS_REFERER
    if target.export_id and target.token:
        referer = (
            "https://channels.weixin.qq.com/finder-preview/pages/feed"
            f"?token={parse.quote(target.token, safe='')}"
            f"&eid={parse.quote(target.export_id, safe='')}"
        )
    elif target.short_uri:
        referer = (
            "https://channels.weixin.qq.com/finder-preview/pages/sph"
            f"?id={parse.quote(target.short_uri, safe='')}"
        )
    return _post_json(
        FEED_INFO_URL,
        body,
        timeout=timeout,
        origin="https://channels.weixin.qq.com",
        referer=referer,
    )


def _exchange_with_yuanbao(share_url: str, cookie: str, *, timeout: int) -> ShareTarget:
    payload = _post_json(
        YUANBAO_PARSE_URL,
        {"type": "video_channel_url", "url": share_url, "scene": 1},
        timeout=timeout,
        origin="https://yuanbao.tencent.com",
        referer="https://yuanbao.tencent.com/",
        extra_headers={"X-Source": "web", "Cookie": cookie},
    )
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    playable = inner.get("playable_url") if isinstance(inner.get("playable_url"), str) else ""
    token = ""
    export_id = ""
    if playable:
        parsed = parse.urlparse(playable)
        query = parse.parse_qs(parsed.query)
        token = _first_query(query, "token")
        export_id = _first_query(query, "eid") or _first_query(query, "exportId")
    if not export_id and isinstance(inner.get("wx_export_id"), str):
        export_id = inner["wx_export_id"].strip()
    if not token or not export_id:
        raise ParserResponseError(
            "Parser response did not contain an H.264 or H.265 video URL."
        )
    return ShareTarget(
        short_uri="",
        export_id=export_id,
        token=token,
        exchange_url=share_url,
    )


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    origin: str,
    referer: str,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": referer,
        "User-Agent": _BROWSER_USER_AGENT,
    }
    if extra_headers:
        headers.update(extra_headers)
    outbound = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(
            outbound,
            timeout=timeout,
            context=_build_ssl_context(),
        ) as response:
            raw_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        raise ParserRequestError(
            f"Parser request failed with HTTP {exc.code}: {body or exc.reason}"
        ) from exc
    except (error.URLError, OSError) as exc:
        raise ParserRequestError(f"Parser request failed: {exc}") from exc

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ParserResponseError("Parser response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ParserResponseError("Parser response was not a JSON object.")
    return data


def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if hasattr(context, "set_alpn_protocols"):
        context.set_alpn_protocols(["http/1.1"])
    return context


def _write_metadata(profile: dict[str, Any], metadata_path: Path) -> None:
    try:
        metadata_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        raise WeixinAudioExtractorError(
            f"Failed to write metadata file: {metadata_path}"
        ) from exc


def _download_file(
    source_url: str,
    destination: Path,
    *,
    timeout: int,
    progress: ProgressCallback | None = None,
    referer: str | None = None,
) -> None:
    headers = {
        "User-Agent": _BROWSER_USER_AGENT,
        "Accept": "*/*",
        "Referer": referer or "https://weixin.qq.com/",
    }
    download_request = request.Request(source_url, headers=headers)
    try:
        with request.urlopen(
            download_request,
            timeout=timeout,
            context=_build_ssl_context(),
        ) as response:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            done = 0
            next_report = 0
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    if progress and (done >= next_report or (total is not None and done >= total)):
                        progress(done, total)
                        next_report = done + 256 * 1024
            if progress:
                progress(done, total)
    except (error.URLError, OSError) as exc:
        if destination.exists():
            destination.unlink()
        raise DownloadError(f"Failed to download video from {source_url}") from exc


def _extract_audio_with_ffmpeg(video_path: Path, audio_path: Path, codec: str = "mp3") -> None:
    ffmpeg_executable = shutil.which("ffmpeg")
    if not ffmpeg_executable:
        raise ExtractionError("ffmpeg not found")
    if codec == "m4a":
        audio_args = ["-vn", "-c:a", "aac", "-b:a", "192k"]
    else:
        audio_args = ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]
    command = [
        ffmpeg_executable,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        *audio_args,
        str(audio_path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error_output = completed.stderr.strip() or completed.stdout.strip() or "ffmpeg failed"
        raise ExtractionError(error_output)
