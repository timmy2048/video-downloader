# Ported from https://github.com/timmy2048/weixin-audio-extractor
# Copyright (c) 2026 timmy2048
# SPDX-License-Identifier: MIT

from .core import (
    DownloadError,
    ExtractionError,
    ExtractionResult,
    MediaResult,
    ParserRequestError,
    ParserResponseError,
    WeixinAudioExtractorError,
    build_media_filename,
    build_output_stem,
    download_media,
    extract_weixin_audio,
    fetch_video_profile,
    select_video_url,
    summarize_profile,
    user_message,
)

__all__ = [
    "DownloadError",
    "ExtractionError",
    "ExtractionResult",
    "MediaResult",
    "ParserRequestError",
    "ParserResponseError",
    "WeixinAudioExtractorError",
    "build_media_filename",
    "build_output_stem",
    "download_media",
    "extract_weixin_audio",
    "fetch_video_profile",
    "select_video_url",
    "summarize_profile",
    "user_message",
]
