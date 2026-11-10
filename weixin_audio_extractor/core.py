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

给定 https://weixin.qq.com/sph/... ，向第三方解析服务
https://sph.litao.workers.dev/api/fetch_video_profile 要资料，
优先用 feedInfo.h264VideoInfo.videoUrl，否则 h265，再否则 feedInfo.videoUrl，
下载 MP4，并可用本机 ffmpeg 抽出 MP3/M4A。

这里不实现新的解析接口，也不解密视频。当前 yt-dlp 没有微信视频号提取器，
所以仍走上述解析服务。音频转换使用系统 PATH 里的 ffmpeg，不另带一份 ffmpeg。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import ssl
import subprocess
from typing import Any, Callable
from urllib import error, parse, request


DEFAULT_PARSER_ENDPOINT = "https://sph.litao.workers.dev/api/fetch_video_profile"
DEFAULT_TIMEOUT_SECONDS = 30
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


def fetch_video_profile(
    share_url: str,
    *,
    parser_endpoint: str = DEFAULT_PARSER_ENDPOINT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    _validate_share_url(share_url)
    payload = json.dumps({"url": share_url}).encode("utf-8")
    parser_request = request.Request(
        parser_endpoint,
        data=payload,
        headers={
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/json",
            "Origin": "https://weixin.qq.com",
            "Referer": "https://weixin.qq.com/",
            "User-Agent": _BROWSER_USER_AGENT,
        },
        method="POST",
    )

    try:
        with request.urlopen(
            parser_request,
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
    """把核心异常翻成页面和命令行用的中文说明，并点明第三方解析服务。"""
    if isinstance(exc, ParserRequestError):
        return (
            "微信视频号的第三方解析服务暂时无法访问"
            f"（{DEFAULT_PARSER_ENDPOINT}）。"
            "这个地址不属于本程序；服务宕机、超时或拒绝请求时，视频号链接会失败。"
            "请稍后重试。如果只有视频号失败、其他网站正常，就是该解析服务的问题。"
        )
    if isinstance(exc, ParserResponseError):
        text = str(exc)
        if "feedInfo" in text or "H.264" in text or "H.265" in text:
            return (
                "第三方解析服务有响应，但没有返回可用的视频地址"
                "（需要 feedInfo.h264VideoInfo.videoUrl，否则用 h265，再否则 feedInfo.videoUrl）。"
                "链接可能已失效，或解析服务改了返回格式。"
                f"解析服务：{DEFAULT_PARSER_ENDPOINT}"
            )
        return (
            "第三方解析服务返回了无法识别的内容。请确认这是 weixin.qq.com/sph/ 开头的视频号分享链接。"
            f"解析服务：{DEFAULT_PARSER_ENDPOINT}"
        )
    if isinstance(exc, DownloadError):
        return "已从第三方解析服务拿到视频地址，但下载 MP4 失败。链接可能过期或网络中断，请重新解析后再试。"
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
    parser_endpoint: str = DEFAULT_PARSER_ENDPOINT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ExtractionResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = build_output_stem(share_url)
    audio_path = output_dir / f"{stem}{AUDIO_SUFFIX}"
    metadata_path = output_dir / f"{stem}{METADATA_SUFFIX}"
    video_path = output_dir / f"{stem}{VIDEO_SUFFIX}"

    profile = fetch_video_profile(
        share_url,
        parser_endpoint=parser_endpoint,
        timeout=timeout,
    )
    source_video_url = select_video_url(profile)

    _write_metadata(profile, metadata_path)
    _download_file(source_video_url, video_path, timeout=timeout)

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
    parser_endpoint: str = DEFAULT_PARSER_ENDPOINT,
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
    profile = fetch_video_profile(
        share_url,
        parser_endpoint=parser_endpoint,
        timeout=timeout,
    )
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
            referer=share_url,
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


def _validate_share_url(share_url: str) -> None:
    parsed_url = parse.urlparse(share_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise WeixinAudioExtractorError(
            "Share URL must be an absolute http(s) URL."
        )


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
