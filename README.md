# 🎬 本地视频下载器（YouTube / TikTok / 抖音 / 微信视频号）

一个运行在你自己电脑上的小网页工具：粘贴链接 → 自动识别来源 → 解析出标题、封面、时长、上传者 → 选择清晰度 → 下载到本地。

- 后端：Python 3 + Flask + [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- 微信视频号：移植自 [weixin-audio-extractor](https://github.com/timmy2048/weixin-audio-extractor) 的核心，在本机请求微信自己的预览接口（见下文）
- 前端：单个 HTML 页面（无需构建）
- 默认只监听 `127.0.0.1:5000`，仅本机可访问

> ⚠️ **版权提示**：请只下载你本人拥有版权、或已获得授权/许可（例如 Creative Commons 许可）的内容，并遵守 YouTube、TikTok、抖音、微信视频号等网站的服务条款及当地法律。本工具仅供个人学习和备份自己的内容使用。

## 支持的来源

| 来源 | 怎么处理 | 链接例子 |
|------|----------|----------|
| YouTube | yt-dlp | `https://www.youtube.com/watch?v=...`、`https://youtu.be/...` |
| TikTok | yt-dlp | `https://www.tiktok.com/@user/video/...`、`https://vm.tiktok.com/...` |
| 抖音 | yt-dlp 官方提取器（`DouyinIE`） | `https://www.douyin.com/video/<数字>`、`https://v.douyin.com/<短链>` |
| 微信视频号 | 本机请求微信预览接口，不是 yt-dlp | `https://weixin.qq.com/sph/...` |

程序会先看域名再分流，解析、下载、进度和「已下载的文件」仍是同一套接口。其他 yt-dlp 本来就能下的网站也可以继续粘贴，只是页面文案以这四个来源为准。

抖音短链 `v.douyin.com` 本身不含视频号。当前 yt-dlp 会用 generic 提取器跟随跳转，落到 `douyin.com/video/<id>` 后再交给 `DouyinIE`。带 `modal_id` 或 `/share/video/<id>` 的抖音页面链接，会先改写成这个官方地址，避免提取器认不出来。这里没有自写抖音接口，也不处理加密或 DRM。

查过当前 yt-dlp（本仓库要求 `>=2026.8.19`，开发时为 2026.10.26）：有抖音提取器，**没有**微信视频号 / `weixin.qq.com/sph` 提取器。视频号改为本机直接请求微信预览接口，不再把链接交给外部解析网站。

## 功能

- 粘贴链接点「解析」：显示标题、封面、时长、上传者、来源
- 清晰度选择（YouTube / TikTok / 抖音，由 yt-dlp 提供的格式决定）：
  - 最佳画质（MP4，视频+音频合并）
  - 1080p / 720p / 480p（视频本身有该清晰度时才会出现）
  - 仅音频 MP3（需要 ffmpeg）/ 仅音频 M4A
- 微信视频号只有微信返回的**一个** MP4，没有 1080p/720p/480p 分档。可以选择：
  - 视频 MP4（保留原片）
  - 仅音频 MP3 或 M4A（需要本机 ffmpeg）
  - 提取音频时可以勾选「提取音频后仍保留 MP4」
- 点击「下载」后，文件进项目内的 `downloads/`，页面显示进度；视频号在能读到文件大小时显示百分比
- 「已下载的文件」列表：可再次下载或删除。视频号还会写一份解析结果 JSON
- 出错时显示中文说明，可展开查看原始错误。视频号失败时会说明是预览里没有播放地址、链接失效，还是网络连不上微信

## 快速开始（一键启动）

前提：已安装 **Python 3.9 或更高版本**。

| 系统 | 操作 |
|------|------|
| Windows | 双击 `start.bat` |
| macOS / Linux | 终端进入项目目录，运行 `bash start.sh`（或 `chmod +x start.sh && ./start.sh`） |

脚本会自动：创建虚拟环境 `venv` → 安装/更新依赖 → 启动程序 → 打开浏览器 http://127.0.0.1:5000 。
关闭命令行窗口（或按 `Ctrl+C`）即可停止。

## 手动安装步骤

### Windows

1. 安装 Python：到 https://www.python.org/downloads/ 下载安装包，安装时**勾选 “Add python.exe to PATH”**。
2. （可选，推荐）安装 ffmpeg，任选一种：
   - `winget install Gyan.FFmpeg`
   - 或从 https://www.gyan.dev/ffmpeg/builds/ 下载，解压后把 `bin` 目录加入系统 PATH
3. 打开「命令提示符」或 PowerShell，进入项目目录：
   ```bat
   cd 路径\video-downloader
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```
4. 浏览器打开 http://127.0.0.1:5000

### macOS / Linux

1. 安装 Python 3：
   - macOS：`brew install python`（或从 python.org 下载安装包）
   - Ubuntu/Debian：`sudo apt install python3 python3-venv python3-pip`
2. （可选，推荐）安装 ffmpeg：
   - macOS：`brew install ffmpeg`
   - Ubuntu/Debian：`sudo apt install ffmpeg`
3. 终端进入项目目录：
   ```bash
   cd /path/to/video-downloader
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   python app.py
   ```
4. 浏览器打开 http://127.0.0.1:5000

## 关于 ffmpeg（可选，但强烈推荐）

YouTube 的 720p 以上画质通常是**视频和音频分开**的，需要 ffmpeg 把它们合并成一个 MP4。微信视频号提取 MP3/M4A 也用这一份系统 ffmpeg，不再额外打包 imageio-ffmpeg。

- **已安装 ffmpeg**：下载最佳画质的视频+音频并合并为 MP4；同等分辨率下优先选择 H.264 + AAC，兼容性最好。注意「最佳画质」若高于 1080p（如 4K），YouTube 只提供 VP9/AV1 编码，个别老旧播放器可能无法播放，此时请选 1080p。可转换 MP3，也可从视频号 MP4 抽出 MP3/M4A。
- **未安装 ffmpeg**：程序会自动退回到「单文件格式」（`best[ext=mp4]/best`，即已经包含音视频的单个文件）。可以正常下载视频，但 YouTube 上通常只有 360p 左右，且**无法转换 MP3**（YouTube/TikTok/抖音的 M4A 仍可能直接下载）。视频号只能保存 MP4，不能提取音频。页面顶部会显示提示。

安装 ffmpeg 后**重启程序**即可生效。

## 保持 yt-dlp 为最新版（重要）

YouTube、TikTok、抖音等网站经常改版，旧版 yt-dlp 可能突然无法解析。遇到解析失败时，请先更新：

```bash
# 先激活虚拟环境 (Windows: venv\Scripts\activate ; macOS/Linux: source venv/bin/activate)
pip install -U yt-dlp
```

（一键启动脚本每次启动都会自动执行升级。）

另外，新版 yt-dlp 解析 YouTube 需要一个 JavaScript 运行时。程序会自动检测并使用本机的 **deno / node / bun / quickjs** 之一；如果都没有，建议安装 [Deno](https://deno.com/)（或 Node.js），否则部分 YouTube 视频可能只能获取到较少的格式。

## 抖音 cookies

抖音经常在没有 cookies 时返回 403，或提示需要 fresh cookies（不一定要登录账号）。这和 TikTok 类似，用的是同一套配置：

- 把浏览器导出的 Netscape 格式 `cookies.txt` 放到项目根目录（与 `app.py` 同级），程序会自动使用；
- 或启动前设置环境变量 `YTDLP_COOKIES_FROM_BROWSER=chrome`（也可以是 `firefox`、`edge` 等）。

没有单独的抖音 cookie 开关。cookies 等同于你的登录凭证，切勿分享给他人。

## 微信视频号

视频号分享链接在本机直接请求微信自己的预览接口，不再使用 `sph.litao.workers.dev`。那个公开地址现在会返回 `401 {"error":"unauthorized"}`，凭证由部署者自己设定，本仓库没有、也不会内置任何密钥。

支持的形式：

- `https://weixin.qq.com/sph/<id>`
- `https://channels.weixin.qq.com/finder-preview/pages/sph?id=<id>`
- 已经带 `token` 和 `eid` 的 `channels.weixin.qq.com/finder-preview/pages/feed?...` 预览页

流程：

1. 短链和 `pages/sph?id=` 把 id 作为 `shortUri`，POST 到 `https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info`。这和微信公开预览页自己发的请求相同。
2. 带 `token` 和 `eid` 的预览页则把它们作为 `generalToken` 和 `exportId` 发给同一个接口。
3. 优先使用 `feedInfo.h264VideoInfo.videoUrl`，否则 `h265VideoInfo.videoUrl`，再否则 `feedInfo.videoUrl`。
4. 下载 MP4 时带 `Referer: https://channels.weixin.qq.com/`，并写出 `<stem>.json`。
5. 若选择音频，用本机 ffmpeg 抽出 MP3 或 M4A；默认删掉 MP4，勾选保留时留下。

公开短链的预览经常只有标题、作者和封面，**没有**可播放地址。实测 `https://weixin.qq.com/sph/AYLHoWrWfu` 会跳到预览页，接口返回 201 和文案、作者、封面，但没有 `videoUrl`。用返回的 `dynamicExportId`、空 token 再请求时，微信回答「此内容暂时无法播放」。需要登录才能看、已经删除或过期的分享同样拿不到 MP4。图集不是视频，会被拒绝。公众号文章（`mp.weixin.qq.com`）不是视频号分享链接，也会被直接拒绝。

若希望普通 `weixin.qq.com/sph/...` 短链也能换成播放地址，可以在本机提供**你自己的**元宝网页 Cookie（登录 https://yuanbao.tencent.com 后从浏览器复制整段 Cookie）：

- 环境变量 `YUANBAO_COOKIE`，或
- 项目根目录的 `yuanbao_cookie.txt`（一行，已在 `.gitignore` 里）

程序只用它向 `yuanbao.tencent.com` 换取预览 token，再调用上面的微信接口。Cookie 不会写进日志，也不会发给别的网站。不要把 Cookie 提交到仓库、发给别人，或做成公开服务；元宝账号有被限制的风险，建议用小号。没有 Cookie 时，程序不会去猜密钥。

命令行仍然可用（网页是主要入口）：

```bash
python -m weixin_audio_extractor --url "https://weixin.qq.com/sph/AWU0OMKvVm" --output-dir downloads
python -m weixin_audio_extractor --url "https://weixin.qq.com/sph/AWU0OMKvVm" --output-dir downloads --keep-video
```

`scripts/extract_weixin_audio.py` 是同一命令的薄入口。

## 常见问题

- **提示 “YouTube 要求验证你不是机器人” / TikTok 或抖音拒绝请求（403）**：网站限制了你当前的 IP（常见于服务器、VPN、机房网络或短时间请求过多），抖音还经常只是缺 cookies。可以稍后再试、换个网络，或使用上面的 `cookies.txt` / `YTDLP_COOKIES_FROM_BROWSER`。
- **视频号提示没有可播放地址**：微信的公开预览没有返回 MP4。链接可能失效、内容需要登录，或只是图集。可以改贴带 `token` 和 `eid` 的完整预览链接，或按上面的说明配置自己的元宝 Cookie。这不是 yt-dlp 的问题。
- **视频号提示连接失败**：本机访问 `channels.weixin.qq.com` 超时或被拒绝。请检查网络后重试。
- **某些地区无法访问 YouTube/TikTok/抖音**：需要你的电脑本身能正常打开这些网站。
- **想换端口**：设置环境变量 `PORT`，例如 `PORT=8080 python app.py`（Windows: `set PORT=8080` 后再运行）。监听地址默认是 `127.0.0.1`，不要改成 `0.0.0.0`。
- **文件保存在哪里**：项目目录下的 `downloads/` 文件夹；浏览器也会另存一份到你的默认下载目录。

## 项目结构

```
video-downloader/
├── app.py                      # Flask：解析 / 下载任务 / 进度 / 文件列表
├── sources.py                  # YouTube / TikTok / 抖音 / 视频号 的链接识别
├── weixin_audio_extractor/     # 视频号核心（解析、下载、ffmpeg、可选 CLI）
├── scripts/extract_weixin_audio.py
├── static/index.html           # 前端页面
├── tests/                      # 不访问抖音或视频号的单元测试
├── requirements.txt
├── start.bat                   # Windows 一键启动
├── start.sh                    # macOS / Linux 一键启动
└── downloads/                  # 下载的文件 (自动创建)
```

## 测试

不访问抖音或微信。安装依赖后：

```bash
python -m unittest discover -s tests -v
```

## API（供参考）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | ffmpeg / yt-dlp 版本 / 是否配置了 cookies |
| POST | `/api/parse` | `{"url": "..."}` → 标题、封面、时长、上传者、来源、可选清晰度 |
| POST | `/api/download` | `{"url": "...", "quality": "best/1080/720/480/mp3/m4a", "keep_video": false}` → `task_id`。`1080/720/480` 只用于 yt-dlp 来源；`keep_video` 只在视频号提取音频时保留 MP4 |
| GET | `/api/progress/<task_id>` | 下载进度 |
| GET | `/api/files` | 已下载文件列表 |
| GET | `/files/<文件名>` | 以附件形式下载文件 |
| DELETE | `/api/files/<文件名>` | 删除文件 |

> 安全说明：服务只绑定 `127.0.0.1`，不要改成 `0.0.0.0` 暴露到局域网/公网。
