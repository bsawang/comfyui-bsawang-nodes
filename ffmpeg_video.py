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


def _video_info(path: str) -> dict:
    """PyAV 读取视频元信息：宽高/时长/fps/总帧数。"""
    import av
    with av.open(path) as c:
        v = c.streams.video[0]
        w = v.codec_context.width or 0
        h = v.codec_context.height or 0
        fps = float(v.average_rate) if v.average_rate else 0.0
        if v.duration:
            dur = float(v.duration * v.time_base)
        elif c.duration:
            dur = float(c.duration / av.time_base)
        else:
            dur = 0.0
        frames = int(v.frames) if v.frames else int(dur * fps + 0.5)
    return {"w": w, "h": h, "dur": dur, "fps": fps, "frames": frames}


def _fmt_video_info(tag: str, info: dict) -> str:
    """格式化视频信息行：视频1  1280×720 | 8.4s | 24fps | 201帧。"""
    return (f"{tag}  {info['w']}×{info['h']} | {info['dur']:.1f}s "
            f"| {info['fps']:.2f}fps | {info['frames']}帧")


def _copy_to_temp(src: str, temp_dir: str) -> str:
    """把视频放到 ComfyUI temp 目录，返回文件名（前端 /view type=temp 引用）。"""
    if os.path.abspath(src).startswith(os.path.abspath(temp_dir)):
        return os.path.basename(src)
    dst = os.path.join(temp_dir, os.path.basename(src))
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
    return os.path.basename(dst)


def _resolve_video_path(video) -> str:
    """把 VIDEO 输入解析为文件路径：BytesIO 先落临时文件（ffmpeg 只认路径）。"""
    src = video.get_stream_source()
    if isinstance(src, str):
        return src
    # BytesIO → 临时文件
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="ffmpeg_vid_src_")
    os.close(fd)
    with open(path, "wb") as f:
        src.seek(0)
        f.write(src.read())
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
        src = _resolve_video_path(视频)
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


class VideoComparePreview:
    """视频对比预览（合并版）：一路源=纯预览，双路源=合并对比预览。

    预览直接在节点上播放（ComfyUI PreviewVideo 机制），预览下方显示各路视频信息。
    双路合成：排列 横/竖 对齐较短边；时长对齐 长(短视频循环铺满)/短(长视频截断)；
    FPS对齐 高(低fps补帧)/低(高fps抽帧)。纯展示节点，输出无；要落盘请用 视频对比+SaveVideo。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "排列方式": (["横", "竖"], {"default": "横",
                                      "tooltip": "横=并排(高度对齐到较短)，竖=上下(宽度对齐到较短)"}),
                "时长对齐": (["短", "长"], {"default": "短",
                                      "tooltip": "短=截断到较短时长；长=短视频循环铺满到较长时长"}),
                "FPS对齐": (["低", "高"], {"default": "低",
                                     "tooltip": "低=抽帧到较低fps；高=补帧到较高fps"}),
            },
            "optional": {
                "视频1": ("VIDEO", {"tooltip": "第一路（可选）；只接一路=纯预览"}),
                "视频2": ("VIDEO", {"tooltip": "第二路（可选）；两路都接=合并对比预览"}),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "preview"
    OUTPUT_NODE = True
    CATEGORY = "bsawang/视频"

    def preview(self, 排列方式, 时长对齐, FPS对齐, 视频1=None, 视频2=None):
        try:
            import folder_paths
            temp_dir = folder_paths.get_temp_directory()
        except Exception:
            temp_dir = tempfile.gettempdir()

        if 视频1 is None and 视频2 is None:
            return {"ui": {"video_info": ["未连接视频源"]}, "result": ()}

        lines = []
        if 视频2 is None:
            # 一路源：纯预览（保留原视频，含音频）
            src = _resolve_video_path(视频1)
            info = _video_info(src)
            lines.append(_fmt_video_info("视频1", info))
            dst = _copy_to_temp(src, temp_dir)
            return {
                "ui": {"images": [{"filename": dst, "subfolder": "", "type": "temp"}],
                       "animated": (True,),
                       "video_info": lines},
                "result": (),
            }
        if 视频1 is None:
            src = _resolve_video_path(视频2)
            info = _video_info(src)
            lines.append(_fmt_video_info("视频2", info))
            dst = _copy_to_temp(src, temp_dir)
            return {
                "ui": {"images": [{"filename": dst, "subfolder": "", "type": "temp"}],
                       "animated": (True,),
                       "video_info": lines},
                "result": (),
            }

        # 双路源：合并对比
        src1 = _resolve_video_path(视频1)
        src2 = _resolve_video_path(视频2)
        i1 = _video_info(src1)
        i2 = _video_info(src2)
        lines.append(_fmt_video_info("视频1", i1))
        lines.append(_fmt_video_info("视频2", i2))

        target_dur = max(i1["dur"], i2["dur"]) if 时长对齐 == "长" else min(i1["dur"], i2["dur"])
        target_fps = max(i1["fps"], i2["fps"]) if FPS对齐 == "高" else min(i1["fps"], i2["fps"])
        target_dur = target_dur if target_dur > 0 else 5.0
        target_fps = target_fps if target_fps > 0 else 24.0

        out_path = _temp_mp4()
        fps_f = f"fps={target_fps},"
        if 排列方式 == "横":  # 并排：高度对齐到较短
            target_h = min(i1["h"], i2["h"])
            target_h = max(2, target_h - target_h % 2)
            vf = (f"[0:v]{fps_f}scale=-2:{target_h}[a];"
                  f"[1:v]{fps_f}scale=-2:{target_h}[b];"
                  f"[a][b]hstack=2[out]")
        else:  # 上下：宽度对齐到较短
            target_w = min(i1["w"], i2["w"])
            target_w = max(2, target_w - target_w % 2)
            vf = (f"[0:v]{fps_f}scale={target_w}:-2[a];"
                  f"[1:v]{fps_f}scale={target_w}:-2[b];"
                  f"[a][b]vstack=2[out]")

        ffmpeg = _find_ffmpeg()
        cmd = [ffmpeg, "-y"]
        if 时长对齐 == "长":  # 短视频循环铺满（-stream_loop 紧跟 -i 前）
            if i1["dur"] < i2["dur"]:
                cmd += ["-stream_loop", "-1", "-i", src1, "-i", src2]
            else:
                cmd += ["-i", src1, "-stream_loop", "-1", "-i", src2]
        else:  # 截断到短时长
            cmd += ["-i", src1, "-i", src2]
        cmd += ["-filter_complex", vf,
                "-map", "[out]", "-map", "0:a:0?",
                "-t", f"{target_dur:.3f}",
                "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23",
                "-c:a", "aac", "-movflags", "+faststart", out_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            cmd[cmd.index("-c:v") + 1] = "libx264"
            subprocess.run(cmd, check=True, capture_output=True)

        out_info = _video_info(out_path)
        lines.append(_fmt_video_info("对比", out_info))
        dst = _copy_to_temp(out_path, temp_dir)
        return {
            "ui": {"images": [{"filename": dst, "subfolder": "", "type": "temp"}],
                   "animated": (True,),
                   "video_info": lines},
            "result": (),
        }


NODE_CLASS_MAPPINGS = {
    "FfmpegVideoPreprocess": FfmpegVideoPreprocess,
    "VideoComparePreview": VideoComparePreview,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "FfmpegVideoPreprocess": "ffmpeg视频预处理",
    "VideoComparePreview": "视频对比预览",
}
