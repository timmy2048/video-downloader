# Ported from https://github.com/timmy2048/weixin-audio-extractor
# Copyright (c) 2026 timmy2048
# SPDX-License-Identifier: MIT

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .core import WeixinAudioExtractorError, extract_weixin_audio, user_message


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从微信视频号分享链接提取 MP3（依赖第三方解析服务）。"
    )
    parser.add_argument("--url", required=True, help="微信视频号分享链接，例如 https://weixin.qq.com/sph/...")
    parser.add_argument(
        "--output-dir",
        default=".",
        help="输出目录，默认当前目录。",
    )
    parser.add_argument(
        "--keep-video",
        action="store_true",
        help="提取 MP3 后保留下载的 MP4。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    try:
        result = extract_weixin_audio(
            args.url,
            Path(args.output_dir).expanduser().resolve(),
            keep_video=args.keep_video,
        )
    except WeixinAudioExtractorError as exc:
        print(f"error: {user_message(exc)}", file=sys.stderr)
        print(f"detail: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
