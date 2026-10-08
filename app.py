# -*- coding: utf-8 -*-
"""
本地视频下载器 (YouTube / TikTok 等)
后端: Flask + yt-dlp
默认只监听 127.0.0.1:5000, 仅供本机使用。
"""
import os
import re
import shutil
import threading
import time
import uuid
import webbrowser
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, request, send_from_directory

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))

# 可选: 放一个 Netscape 格式的 cookies.txt 到项目目录, 或设置环境变量
# YTDLP_COOKIES_FROM_BROWSER=chrome / firefox / edge ... 用于应对 "请登录以确认你不是机器人" 之类的限制
COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
COOKIES_FROM_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False

TASKS = {}
TASKS_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def ffmpeg_path():
    return shutil.which("ffmpeg")


def has_ffmpeg():
    return ffmpeg_path() is not None


def detect_js_runtimes():
    """YouTube 新版需要 JS 运行时 (deno/node/bun/quickjs) 来解析签名, 检测本机已安装的。"""
    runtimes = {}
    for name, exe in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
        p = shutil.which(exe)
        if p:
            runtimes[name] = {"path": p}
    return runtimes


JS_RUNTIMES = detect_js_runtimes()


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "windowsfilenames": True,
        "color": {"stdout": "never", "stderr": "never"},
    }
    if JS_RUNTIMES:
        opts["js_runtimes"] = JS_RUNTIMES
    if os.path.isfile(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    elif COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    return opts


def validate_url(url):
    if not isinstance(url, str):
        return None, "请输入视频链接"
    url = url.strip()
    if not url:
        return None, "请输入视频链接"
    if len(url) > 2048:
        return None, "链接过长"
    try:
        p = urlparse(url)
    except ValueError:
        return None, "链接格式不正确"
    if p.scheme not in ("http", "https") or not p.netloc:
        return None, "只支持 http:// 或 https:// 开头的链接"
    return url, None


def clean_error(e):
    msg = ANSI_RE.sub("", str(e)).strip()
    msg = re.sub(r"^ERROR:\s*", "", msg)
    return msg


def friendly_error(e):
    raw = clean_error(e)
    low = raw.lower()
    hint = None
    if "not a bot" in low or "sign in to confirm" in low:
        hint = "YouTube 要求验证你不是机器人 (常见于服务器/机房 IP 或频繁请求)。可稍后重试、更换网络, 或在项目目录放置 cookies.txt。"
    elif "universal data for rehydration" in low or ("tiktok" in low and ("403" in low or "forbidden" in low)):
        hint = "TikTok 拒绝了请求 (当前网络/IP 可能被 TikTok 限制或所在地区不可用, 也可能视频已删除)。可更换网络后重试, 并确保 yt-dlp 为最新版: pip install -U yt-dlp"
    elif "unsupported url" in low:
        hint = "不支持该链接, 请确认是有效的 YouTube / TikTok 视频地址。"
    elif "private" in low:
        hint = "该视频为私密视频, 无法下载。"
    elif "age" in low and ("restricted" in low or "confirm your age" in low):
        hint = "该视频有年龄限制, 需要登录 cookies 才能访问。"
    elif "not available" in low or "unavailable" in low:
        hint = "视频不可用 (可能已删除、地区限制或需要登录)。"
    elif "ip address is blocked" in low or "blocked" in low or "403" in low:
        hint = "请求被网站拒绝 (IP 可能被限制)。可更换网络或稍后再试, 并确保 yt-dlp 为最新版: pip install -U yt-dlp"
    elif "timed out" in low or "timeout" in low or "connection" in low or "resolve" in low:
        hint = "网络连接失败或超时, 请检查网络 (部分网站在某些地区需要代理)。"
    elif "requested format is not available" in low:
        hint = "所选清晰度不可用, 请换一个清晰度再试。"
    elif "ffmpeg" in low:
        hint = "需要 ffmpeg 才能完成该操作, 请安装 ffmpeg 后重启程序。"
    msg = hint or "解析/下载失败, 请检查链接或更新 yt-dlp (pip install -U yt-dlp)。"
    return {"error": msg, "detail": raw}


def short_side(f):
    w, h = f.get("width"), f.get("height")
    if w and h:
        return min(w, h)
    return h or w


def build_quality_options(info):
    ff = has_ffmpeg()
    formats = info.get("formats") or [info]
    sides = [short_side(f) for f in formats if f.get("vcodec") not in (None, "none") or f.get("height")]
    sides = [s for s in sides if s]
    max_side = max(sides) if sides else None

    if ff:
        opts = [{"id": "best", "label": "最佳画质 (MP4, 视频+音频)"}]
    else:
        opts = [{"id": "best", "label": "最佳可用 (单文件 MP4, 未安装 ffmpeg)"}]
    for h in (1080, 720, 480):
        if max_side and max_side >= h:
            opts.append({"id": str(h), "label": f"{h}p (MP4)" + ("" if ff else " - 不高于此清晰度的单文件")})
    opts.append({"id": "mp3", "label": "仅音频 MP3" + ("" if ff else " (需 ffmpeg)"), "disabled": not ff})
    opts.append({"id": "m4a", "label": "仅音频 M4A"})
    return opts, max_side


def format_opts(quality):
    """根据所选清晰度返回 yt-dlp 参数。有 ffmpeg 时下载分离的音视频并合并为 mp4; 否则退回单文件格式。"""
    ff = has_ffmpeg()
    o = {}
    if quality in ("mp3", "m4a"):
        if quality == "mp3":
            if not ff:
                raise ValueError("转换 MP3 需要 ffmpeg, 请安装 ffmpeg 或选择 M4A。")
            o["format"] = "ba/b"
            o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        else:
            if ff:
                o["format"] = "ba[ext=m4a]/ba/b"
                o["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
            else:
                o["format"] = "ba[ext=m4a]/b[ext=m4a]/ba[ext=mp4]"
        return o

    height = None
    if quality != "best":
        if quality not in ("1080", "720", "480"):
            raise ValueError("未知的清晰度选项")
        height = int(quality)

    if ff:
        o["format"] = "bv*+ba/b"
        # res 为短边分辨率, 竖屏视频同样适用; 同分辨率下优先 H.264 + m4a(AAC), 兼容性最好, 可直接合并为 mp4
        o["format_sort"] = [f"res:{height}" if height else "res", "vcodec:h264", "ext:mp4:m4a"]
        o["merge_output_format"] = "mp4"
    else:
        # 无 ffmpeg: 只能下载已包含音视频的单个文件
        if height:
            o["format"] = f"b[height<={height}][ext=mp4]/b[height<={height}]/b[ext=mp4]/b"
        else:
            o["format"] = "best[ext=mp4]/best"
    return o


QUALITY_TAGS = {"best": " [best]", "1080": " [1080p]", "720": " [720p]", "480": " [480p]", "mp3": "", "m4a": ""}


def human_size(n):
    if n is None:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def safe_name(name):
    """只允许访问 downloads 目录下的普通文件。"""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = os.path.join(DOWNLOAD_DIR, name)
    if not os.path.isfile(path):
        return None
    return name


# ----------------------------------------------------------------------------
# 下载任务
# ----------------------------------------------------------------------------
def update_task(task_id, **kw):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if t is not None:
            t.update(kw)
            t["updated"] = time.time()


def run_download(task_id, url, quality):
    seen_files = []

    def progress_hook(d):
        fname = d.get("filename") or ""
        if fname and fname not in seen_files:
            seen_files.append(fname)
        part = len(seen_files)
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else None
            update_task(
                task_id,
                status="downloading",
                percent=round(pct, 1) if pct is not None else None,
                downloaded=human_size(done),
                total=human_size(total) if total else "",
                speed=(human_size(d.get("speed")) + "/s") if d.get("speed") else "",
                eta=d.get("eta"),
                part=part,
                message=f"正在下载{'(第 %d 部分)' % part if part > 1 else ''}…",
            )
        elif d["status"] == "finished":
            update_task(task_id, percent=100, message="下载完成, 正在处理…")

    def pp_hook(d):
        if d["status"] == "started":
            names = {
                "Merger": "正在合并音视频…",
                "FFmpegMerger": "正在合并音视频…",
                "FFmpegExtractAudio": "正在提取/转换音频…",
                "MoveFiles": "正在整理文件…",
            }
            update_task(task_id, status="processing", message=names.get(d.get("postprocessor"), "正在处理…"))

    try:
        opts = base_opts()
        opts.update(format_opts(quality))
        opts.update({
            # 文件名中带上清晰度, 避免不同清晰度互相覆盖/被误判为"已下载"
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).80B [%(id)s]" + QUALITY_TAGS[quality] + ".%(ext)s"),
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [pp_hook],
            "overwrites": False,
            "continuedl": True,
        })
        update_task(task_id, status="starting", message="正在获取视频信息…")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            filepath = None
            rds = info.get("requested_downloads") or []
            if rds:
                filepath = rds[0].get("filepath") or rds[0].get("_filename")
            if not filepath:
                filepath = ydl.prepare_filename(info)
        if not filepath or not os.path.isfile(filepath):
            raise RuntimeError("下载完成但未找到输出文件")
        name = os.path.basename(filepath)
        update_task(
            task_id,
            status="finished",
            percent=100,
            message="完成",
            filename=name,
            size=human_size(os.path.getsize(filepath)),
            title=info.get("title"),
        )
    except ValueError as e:
        update_task(task_id, status="error", error=str(e), detail="", message="失败")
    except Exception as e:  # noqa: BLE001 - 所有错误都要友好地返回给前端
        fe = friendly_error(e)
        app.logger.warning("download failed: %s", fe["detail"])
        update_task(task_id, status="error", error=fe["error"], detail=fe["detail"], message="失败")


# ----------------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/status")
def api_status():
    return jsonify({
        "ffmpeg": has_ffmpeg(),
        "yt_dlp_version": yt_dlp.version.__version__,
        "js_runtimes": list(JS_RUNTIMES.keys()),
        "cookies": os.path.isfile(COOKIE_FILE) or bool(COOKIES_FROM_BROWSER),
    })


@app.route("/api/parse", methods=["POST"])
def api_parse():
    data = request.get_json(silent=True) or {}
    url, err = validate_url(data.get("url"))
    if err:
        return jsonify({"error": err}), 400
    try:
        opts = base_opts()
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                return jsonify({"error": "这是一个播放列表/合集链接, 请粘贴单个视频的链接。"}), 400
            info = entries[0]
        qualities, max_side = build_quality_options(info)
        return jsonify({
            "title": info.get("title"),
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration"),
            "duration_string": info.get("duration_string"),
            "uploader": info.get("uploader") or info.get("channel") or info.get("creator"),
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "webpage_url": info.get("webpage_url") or url,
            "max_resolution": max_side,
            "qualities": qualities,
            "ffmpeg": has_ffmpeg(),
        })
    except Exception as e:  # noqa: BLE001
        fe = friendly_error(e)
        app.logger.warning("parse failed: %s", fe["detail"])
        return jsonify(fe), 502


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    url, err = validate_url(data.get("url"))
    if err:
        return jsonify({"error": err}), 400
    quality = str(data.get("quality") or "best")
    if quality not in ("best", "1080", "720", "480", "mp3", "m4a"):
        return jsonify({"error": "未知的清晰度选项"}), 400
    if quality == "mp3" and not has_ffmpeg():
        return jsonify({"error": "转换 MP3 需要 ffmpeg, 请安装 ffmpeg 或选择 M4A。"}), 400
    task_id = uuid.uuid4().hex
    with TASKS_LOCK:
        TASKS[task_id] = {
            "id": task_id, "status": "queued", "percent": 0, "message": "排队中…",
            "url": url, "quality": quality, "created": time.time(), "updated": time.time(),
        }
    threading.Thread(target=run_download, args=(task_id, url, quality), daemon=True).start()
    return jsonify({"task_id": task_id})


@app.route("/api/progress/<task_id>")
def api_progress(task_id):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
        if not t:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(dict(t))


@app.route("/api/files")
def api_files():
    items = []
    for name in os.listdir(DOWNLOAD_DIR):
        p = os.path.join(DOWNLOAD_DIR, name)
        if not os.path.isfile(p) or name.startswith(".") or name.endswith((".part", ".ytdl")):
            continue
        if re.search(r"\.f\d+\.\w+$", name):  # 合并前的临时分段文件
            continue
        st = os.stat(p)
        items.append({
            "name": name,
            "size": human_size(st.st_size),
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "_m": st.st_mtime,
        })
    items.sort(key=lambda x: x["_m"], reverse=True)
    for i in items:
        i.pop("_m")
    return jsonify({"files": items})


@app.route("/files/<path:name>")
def get_file(name):
    name = safe_name(name)
    if not name:
        abort(404)
    return send_from_directory(DOWNLOAD_DIR, name, as_attachment=True, download_name=name)


@app.route("/api/files/<path:name>", methods=["DELETE"])
def delete_file(name):
    name = safe_name(name)
    if not name:
        return jsonify({"error": "文件不存在"}), 404
    os.remove(os.path.join(DOWNLOAD_DIR, name))
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("=" * 56)
    print(f"  视频下载器已启动: http://{HOST}:{PORT}")
    print(f"  yt-dlp {yt_dlp.version.__version__} | ffmpeg: {'已安装' if has_ffmpeg() else '未安装 (将使用单文件格式)'}")
    print(f"  JS 运行时: {', '.join(JS_RUNTIMES) or '未检测到 (YouTube 部分格式可能不可用)'}")
    print(f"  文件保存在: {DOWNLOAD_DIR}")
    print("  按 Ctrl+C 停止")
    print("=" * 56)
    if os.environ.get("OPEN_BROWSER") == "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
