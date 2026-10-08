#!/usr/bin/env bash
# 视频下载器 一键启动 (macOS / Linux)
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else
  echo "未找到 Python 3, 请先安装: https://www.python.org/downloads/"
  echo "  macOS: brew install python    Ubuntu/Debian: sudo apt install python3 python3-venv"
  exit 1
fi

if [ ! -x "venv/bin/python" ]; then
  echo ">>> 创建虚拟环境 venv ..."
  if ! "$PY" -m venv venv; then
    echo "创建虚拟环境失败。Ubuntu/Debian 请先执行: sudo apt install python3-venv"
    exit 1
  fi
fi

echo ">>> 安装/更新依赖 (包括最新版 yt-dlp) ..."
venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
venv/bin/python -m pip install --disable-pip-version-check -q -U -r requirements.txt

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "提示: 未检测到 ffmpeg, 将使用单文件格式 (画质可能较低, 无法转 MP3)。"
  echo "      macOS: brew install ffmpeg    Ubuntu/Debian: sudo apt install ffmpeg"
fi

export OPEN_BROWSER="${OPEN_BROWSER:-1}"
exec venv/bin/python app.py
