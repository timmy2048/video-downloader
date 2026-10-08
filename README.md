# 🎬 本地视频下载器（YouTube / TikTok）

一个运行在你自己电脑上的小网页工具：粘贴 YouTube 或 TikTok 视频链接 → 解析出标题、封面、时长、上传者 → 选择清晰度 → 下载到本地。

- 后端：Python 3 + Flask + [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- 前端：单个 HTML 页面（无需构建）
- 默认只监听 `127.0.0.1:5000`，仅本机可访问

> ⚠️ **版权提示**：请只下载你本人拥有版权、或已获得授权/许可（例如 Creative Commons 许可）的内容，并遵守各网站的服务条款及当地法律。本工具仅供个人学习和备份自己的内容使用。

## 功能

- 粘贴链接点「解析」：显示标题、封面、时长、上传者、最高分辨率
- 清晰度选择：
  - 最佳画质（MP4，视频+音频合并）
  - 1080p / 720p / 480p（视频本身有该清晰度时才会出现）
  - 仅音频 MP3（需要 ffmpeg）/ 仅音频 M4A
- 点击「下载」后，服务器用 yt-dlp 下载到项目内的 `downloads/` 文件夹，页面实时显示进度（百分比、速度、剩余时间、合并/转换状态），完成后浏览器自动接收文件
- 「已下载的文件」列表：可再次下载或删除
- 出错时（链接无效、视频不可用、网站拦截等）在页面上显示友好的中文提示，可展开查看 yt-dlp 原始错误

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

YouTube 的 720p 以上画质通常是**视频和音频分开**的，需要 ffmpeg 把它们合并成一个 MP4。

- **已安装 ffmpeg**：下载最佳画质的视频+音频并合并为 MP4；同等分辨率下优先选择 H.264 + AAC，兼容性最好。注意「最佳画质」若高于 1080p（如 4K），YouTube 只提供 VP9/AV1 编码，个别老旧播放器可能无法播放，此时请选 1080p。可转换 MP3。
- **未安装 ffmpeg**：程序会自动退回到「单文件格式」（`best[ext=mp4]/best`，即已经包含音视频的单个文件）。可以正常使用，但 YouTube 上通常只有 360p 左右，且**无法转换 MP3**（M4A 仍可用）。页面顶部会显示提示。

安装 ffmpeg 后**重启程序**即可生效。

## 保持 yt-dlp 为最新版（重要）

YouTube、TikTok 等网站经常改版，旧版 yt-dlp 可能突然无法解析。遇到解析失败时，请先更新：

```bash
# 先激活虚拟环境 (Windows: venv\Scripts\activate ; macOS/Linux: source venv/bin/activate)
pip install -U yt-dlp
```

（一键启动脚本每次启动都会自动执行升级。）

另外，新版 yt-dlp 解析 YouTube 需要一个 JavaScript 运行时。程序会自动检测并使用本机的 **deno / node / bun / quickjs** 之一；如果都没有，建议安装 [Deno](https://deno.com/)（或 Node.js），否则部分 YouTube 视频可能只能获取到较少的格式。

## 常见问题

- **提示 “YouTube 要求验证你不是机器人” / TikTok 拒绝请求（403）**：网站限制了你当前的 IP（常见于服务器、VPN、机房网络或短时间请求过多）。可以稍后再试、换个网络，或使用 cookies：
  - 把浏览器导出的 Netscape 格式 `cookies.txt` 放到项目根目录（与 `app.py` 同级），程序会自动使用；
  - 或启动前设置环境变量 `YTDLP_COOKIES_FROM_BROWSER=chrome`（也可以是 `firefox`、`edge` 等）。
  - 注意：cookies 等同于你的登录凭证，切勿分享给他人。
- **某些地区无法访问 YouTube/TikTok**：需要你的电脑本身能正常打开这些网站。
- **想换端口**：设置环境变量 `PORT`，例如 `PORT=8080 python app.py`（Windows: `set PORT=8080` 后再运行）。
- **文件保存在哪里**：项目目录下的 `downloads/` 文件夹；浏览器也会另存一份到你的默认下载目录。

## 项目结构

```
video-downloader/
├── app.py            # Flask 后端 (解析 / 下载任务 / 进度 / 文件列表)
├── static/index.html # 前端页面
├── requirements.txt
├── start.bat         # Windows 一键启动
├── start.sh          # macOS / Linux 一键启动
└── downloads/        # 下载的文件 (自动创建)
```

## API（供参考）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | ffmpeg / yt-dlp 版本等环境信息 |
| POST | `/api/parse` | `{"url": "..."}` → 标题、封面、时长、上传者、可选清晰度 |
| POST | `/api/download` | `{"url": "...", "quality": "best/1080/720/480/mp3/m4a"}` → `task_id` |
| GET | `/api/progress/<task_id>` | 下载进度 |
| GET | `/api/files` | 已下载文件列表 |
| GET | `/files/<文件名>` | 以附件形式下载文件 |
| DELETE | `/api/files/<文件名>` | 删除文件 |

> 安全说明：服务只绑定 `127.0.0.1`，不要改成 `0.0.0.0` 暴露到局域网/公网。
