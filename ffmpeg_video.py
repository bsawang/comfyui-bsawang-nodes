# -*- coding: utf-8 -*-
"""ffmpeg 视频预处理节点：一次加载 + 重采样 + 改尺寸 + 转图像 batch。

直接调 ComfyUI 便携版自带 ffmpeg（NVENC 硬件编码），覆盖 H3 链路里
LoadVideo→GetVideoComponents→VideoFrameSample 的整串慢操作：
30fps→24fps 重采样、变尺寸、抽帧全部一 pass 完成，秒级出图。

用法：LoadVideo（保留预览）→ 本节点 → IMAGE 接 H3 ref_video、meta 接 H3 length。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

from comfy_api.latest._input_impl.video_types import VideoFromFile

NODE_DIR = Path(__file__).parent


def _find_ffmpeg() -> str:
    """定位 ffmpeg：python_standalone/Scripts（ComfyUI 便携版自带）→ PATH。"""
    cand = Path(sys.executable).parent / "Scripts" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if cand.exists():
        return str(cand)
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    raise RuntimeError("[ffmpeg视频] 找不到 ffmpeg（python_standalone/Scripts 与 PATH 均无）")


def _temp_mp4() -> str:
    """临时输出 mp4（供 VideoFromFile 引用，生命周期随 temp 目录）。"""
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="ffmpeg_vid_")
    os.close(fd)
    os.remove(path)  # ffmpeg 需要写新文件
    return path


def _decode_video_to_tensor(path: str, max_frames: int | None = None) -> torch.Tensor:
    """PyAV 解码视频 → IMAGE tensor [B,H,W,C]（float 0-1）。"""
    import av
    container = av.open(path)
    stream = container.streams.video[0]
    frames = []
    for frame in container.decode(stream):
        arr = np.asarray(frame.to_image())
        frames.append(torch.from_numpy(arr.copy()).float() / 255.0)
        if max_frames and len(frames) >= max_frames:
            break
    container.close()
    if not frames:
        raise RuntimeError(f"[ffmpeg视频] 解码无帧: {path}")
    return torch.stack(frames)


def _extract_audio(path: str) -> dict:
    """PyAV 解码音轨 → ComfyUI AUDIO dict {waveform:[B,C,L], sample_rate}。"""
    import av
    container = av.open(path)
    try:
        stream = container.streams.audio[0]
        sr = int(stream.rate)
        chunks = []
        for frame in container.decode(stream):
            arr = frame.to_ndarray()  # [C, N]
            if arr.ndim == 1:
                arr = arr[None, :]
            elif arr.ndim == 3:
                arr = arr[0]
            chunks.append(arr)
        if not chunks:
            raise RuntimeError("[ffmpeg视频] 源视频无音轨，无法输出音频")
        data = np.concatenate(chunks, axis=1)  # [C, L]
        waveform = torch.from_numpy(data).float().unsqueeze(0)  # [1, C, L]
    finally:
        container.close()
    return {"waveform": waveform, "sample_rate": sr}


def _build_filters(fps: float, width: int, height: int, crop: str) -> list[str]:
    """按 重采样/改尺寸 需求组装 vf 链；无需求时返回空列表。"""
    parts: list[str] = []
    if fps and fps > 0:
        parts.append(f"fps={fps}")
    if width and height:
        if crop == "crop居中":
            # 等比放大铺满 → 居中裁切
            parts.append(f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}")
        elif crop == "等比缩放":
            # 等比缩小放入 → 黑边补满
            parts.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
        else:  # 拉伸
            parts.append(f"scale={width}:{height}")
    parts.append("format=yuv420p")
    return parts


def _run_ffmpeg(src: str, filters: list[str], out_path: str) -> None:
    """ffmpeg 一 pass：vf 链 + NVENC 编码；NVENC 不可用回退 libx264。"""
    ffmpeg = _find_ffmpeg()
    vf = ",".join(filters)
    cmd = [ffmpeg, "-y", "-i", src, "-vf", vf,
           "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23",
           "-c:a", "aac", "-movflags", "+faststart", out_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        cmd = [ffmpeg, "-y", "-i", src, "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-c:a", "aac", "-movflags", "+faststart", out_path]
        subprocess.run(cmd, check=True, capture_output=True)


class FfmpegVideoPreprocess:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "视频": ("VIDEO", {"tooltip": "源视频（LoadVideo 输出）"}),
                "fps": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 120.0, "step": 0.001,
                                  "tooltip": "重采样目标 fps；0=保持原帧率"}),
                "宽度": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 32,
                                 "tooltip": "目标宽度；0=保持原宽"}),
                "高度": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 32,
                                 "tooltip": "目标高度；0=保持原高"}),
                "裁切": (["crop居中", "等比缩放", "拉伸"], {"default": "crop居中",
                        "tooltip": "改尺寸方式：crop居中=铺满裁切，等比缩放=留黑边，拉伸=变形"}),
            },
            "optional": {
                "图像": ("IMAGE", {"tooltip": "可选：直接提供帧时跳过视频解码（原样透传，不重采样）"}),
            },
        }

    RETURN_TYPES = ("VIDEO", "IMAGE", "AUDIO", "INT", "INT", "FLOAT", "FLOAT", "INT")
    RETURN_NAMES = ("视频", "图像", "音频", "高度", "宽度", "FPS", "时长", "总帧数")
    FUNCTION = "process"
    CATEGORY = "bsawang/视频"

    def process(self, 视频, fps, 宽度, 高度, 裁切, 图像=None):
        src = str(视频.get_stream_source())
        src_fps = float(视频.get_frame_rate())

        if 图像 is not None:
            # 已提供帧：原样透传（不做重采样/改尺寸），meta 仍按视频取
            images = 图像
            out_video = 视频
            out_fps = src_fps
            audio_src = src
        else:
            need_ffmpeg = (fps > 0 and abs(fps - src_fps) > 1e-3) or bool(宽度 and 高度)
            if need_ffmpeg:
                filters = _build_filters(fps, 宽度, 高度, 裁切)
                out_path = _temp_mp4()
                _run_ffmpeg(src, filters, out_path)
                images = _decode_video_to_tensor(out_path)
                out_video = VideoFromFile(out_path)
                out_fps = fps if fps > 0 else src_fps
                audio_src = out_path
            else:
                images = _decode_video_to_tensor(src)
                out_video = 视频
                out_fps = src_fps
                audio_src = src

        frames = int(images.shape[0])
        height = int(images.shape[1])
        width = int(images.shape[2])
        duration = frames / out_fps if out_fps > 0 else 0.0
        audio = _extract_audio(audio_src)
        return (out_video, images, audio, height, width, out_fps, duration, frames)


NODE_CLASS_MAPPINGS = {"FfmpegVideoPreprocess": FfmpegVideoPreprocess}
NODE_DISPLAY_NAME_MAPPINGS = {"FfmpegVideoPreprocess": "ffmpeg视频预处理"}
