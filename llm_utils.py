# -*- coding: utf-8 -*-
"""LLM 通用工具库：三个 LLM 消费节点（提示词增强器 / H3 格式化 / Tag 反推）共用。

职责：系统提示词读取、LLM 调用（Anthropic/OpenAI 兼容 + 多模态）、HTTP POST、
图片转 base64、JSON 提取、文本清理。统一返回 (content, usage_snapshot)，
错误信息带 error_prefix 前缀（保留各节点 [提示词增强器]/[H3-API]/[Tag反推] 区分）。

行为基准：prompt_enhancer 最全实现（seed + thinking disabled + temperature 失败重试 + reasoning 提示）。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import llm_usage


# ---------- 系统提示词 ----------


def pick_template(templates_dir, base_name) -> Path:
    """本地覆盖模板选择（宽规则）：找 templates_dir/{base}.*.txt 变体（任意后缀均可，多后缀取排序后第一个），
    存在即优先；否则回落跟踪的 {base}.txt。代码不依赖具体后缀字面。"""
    variants = sorted(templates_dir.glob(f"{base_name}.*.txt"))
    if variants:
        return variants[0]
    return templates_dir / f"{base_name}.txt"


def read_system_prompt(path_str, default=None, error_prefix=""):
    """读系统提示词文件。路径为空 → 回落 default（None 则报错）；读取失败/空内容显式报错。"""
    path_str = (path_str or "").strip()
    if not path_str:
        if default is not None:
            return default
        raise RuntimeError(f"{error_prefix}系统提示词文件路径为空")
    try:
        with open(path_str, encoding="utf-8") as f:
            content = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"{error_prefix}系统提示词文件读取失败：{path_str}（{e}）")
    if not content:
        raise RuntimeError(f"{error_prefix}系统提示词文件为空：{path_str}")
    return content


# ---------- 文本清理 ----------


def strip_code_fences(content: str) -> str:
    if content.startswith("```"):
        content = content.split("\n", 1)[-1] if "\n" in content else ""
        if content.endswith("```"):
            content = content[:-3].rstrip()
    return content.strip()


def collapse_blank_lines(text: str) -> str:
    """把连续 2+ 空行压缩成 1 个空行（保留分段标题间的单个空行，去掉多余空行）。"""
    lines = text.split("\n")
    result = []
    prev_blank = False
    for line in lines:
        if line.strip() == "":
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    while result and result[0].strip() == "":
        result.pop(0)
    while result and result[-1].strip() == "":
        result.pop()
    return "\n".join(result)


def parse_json(content, error_prefix="") -> dict:
    """从 LLM 输出提取 JSON（容忍代码围栏/尾逗号/注释/字符串内花括号）。"""
    import re
    text = content.strip()
    # 去 markdown 代码围栏
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    # 从第一个 { 括号配平找结束（容忍字符串值里的 { }）
    start = text.find("{")
    if start < 0:
        raise RuntimeError(f"{error_prefix}LLM 输出不含 JSON：{content[:300]}")
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise RuntimeError(f"{error_prefix}JSON 括号未闭合（可能被 max_tokens 截断，请调大「最大token」）：{content[:300]}")
    json_str = text[start : end + 1]
    # 清尾逗号（LLM 常见产物：",}" / ",]"）
    json_str = re.sub(r",\s*([}\]])", r"\1", json_str)
    try:
        return json.loads(json_str)
    except Exception:
        # 回落：去 // 行注释 与 /* */ 块注释（LLM 偶尔加）
        cleaned = re.sub(r"/\*.*?\*/", "", json_str, flags=re.S)
        cleaned = re.sub(r"(?m)//.*$", "", cleaned)
        try:
            return json.loads(cleaned)
        except Exception:
            raise RuntimeError(f"{error_prefix}JSON 解析失败：{json_str[:300]}")


# ---------- 图片 ----------


def image_to_base64(image) -> str:
    """把 ComfyUI IMAGE tensor 转成 base64 JPEG（data URI）。"""
    import base64
    import io

    from PIL import Image

    img = image[0]  # 取第一帧
    arr = img.detach().cpu().numpy()
    if arr.shape[-1] == 4:  # RGBA → RGB
        arr = arr[..., :3]
    arr = (arr * 255).clip(0, 255).astype("uint8")
    pil = Image.fromarray(arr)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ---------- 配置 ----------


def stable_config(config, temp):
    """复制配置并覆盖温度（匹配/归纳用低温，减少飘逸）。"""
    c = dict(config)
    c["温度"] = temp
    return c


# ---------- HTTP ----------


def _post_json(url, headers, payload, strip_temperature_on_error=False, error_prefix=""):
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        if strip_temperature_on_error and e.code in (400, 422) and "temperature" in body:
            payload.pop("temperature", None)
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        raise RuntimeError(f"{error_prefix}HTTP {e.code}：{body[:500]}")
    except Exception as e:
        raise RuntimeError(f"{error_prefix}请求失败：{e}")


# ---------- LLM 调用（统一入口） ----------


def _normalize_images(image_b64):
    """归一化图片参数：None / 单张 str / 多张 list → list（或 None）。"""
    if image_b64 is None:
        return None
    if isinstance(image_b64, (list, tuple)):
        return list(image_b64)
    return [image_b64]


def call_llm(config, system, user_msg, image_b64=None, seed=0, error_prefix=""):
    """统一 LLM 调用：Anthropic 兼容 / OpenAI 兼容 + 多模态。

    config: LLM_API_Configurator 输出的 LLM_CONFIG（接口格式/模型/API基础URL/APIKey环境变量/温度/最大token）。
    image_b64: 单张 data URI 或 data URI 列表（多图）。
    返回 (content, usage_snapshot)。content 已去代码围栏/strip。
    """
    interface = config.get("接口格式") or "Anthropic 兼容"
    model = config.get("模型") or ""
    base_url = (config.get("API基础URL") or "").strip()
    api_key_env = (config.get("APIKey环境变量") or "ANTHROPIC_AUTH_TOKEN").strip()
    temperature = config.get("温度", 0.4)
    max_tokens = int(config.get("最大token", 8192))
    api_key = os.environ.get(api_key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise ValueError(f"{error_prefix}未找到 API key：环境变量「{api_key_env}」为空。")

    if interface.startswith("Anthropic"):
        return _call_anthropic(
            api_key, system, user_msg, model, base_url, temperature, max_tokens,
            image_b64, seed, error_prefix,
        )
    return _call_openai(
        api_key, system, user_msg, model, base_url, temperature, max_tokens,
        image_b64, seed, error_prefix,
    )


def _call_anthropic(
    api_key, system, user_msg, model, base_url, temperature, max_tokens,
    image_b64=None, seed=0, error_prefix="",
):
    url = base_url.rstrip("/")
    if not url.endswith("/v1/messages"):
        url += "/v1/messages"
    # 支持多模态（单张或多张）：image_b64 存在时 content 为 [{type:text}, {type:image}...]
    images = _normalize_images(image_b64)
    user_content = user_msg
    if images:
        user_content = [{"type": "text", "text": user_msg}]
        for b64 in images:
            media_type = b64.split(";", 1)[0].split(":", 1)[1] if ";" in b64 else "image/jpeg"
            data = b64.split(",", 1)[1] if "," in b64 else b64
            user_content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": user_content}],
    }
    if seed:
        payload["seed"] = seed
    payload["temperature"] = temperature
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    data = _post_json(url, headers, payload, strip_temperature_on_error=True, error_prefix=error_prefix)
    text_parts = [
        c.get("text", "")
        for c in data.get("content", [])
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    content = strip_code_fences("".join(text_parts))
    if not content:
        raise RuntimeError(
            f"{error_prefix}Anthropic 接口返回空 text。请检查「最大token」是否太小、"
            "「API基础URL」是否为 Anthropic 兼容地址（.../anthropic）。"
        )
    usage = data.get("usage") or {}
    snap = llm_usage.record(usage.get("input_tokens"), usage.get("output_tokens"))
    return content, snap


def _call_openai(
    api_key, system, user_msg, model, base_url, temperature, max_tokens,
    image_b64=None, seed=0, error_prefix="",
):
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    # 支持多模态（单张或多张）：image_b64 存在时 user content 为 [{type:text}, {type:image_url}...]
    images = _normalize_images(image_b64)
    user_content = user_msg
    if images:
        user_content = [{"type": "text", "text": user_msg}]
        for b64 in images:
            user_content.append({"type": "image_url", "image_url": {"url": b64}})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if seed:
        payload["seed"] = seed
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data = _post_json(url, headers, payload, strip_temperature_on_error=False, error_prefix=error_prefix)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"{error_prefix}响应结构异常：{json.dumps(data, ensure_ascii=False)[:500]}")
    content = strip_code_fences((msg.get("content") or "").strip())
    if not content:
        reasoning = (msg.get("reasoning_content") or "").strip()
        hint = f"（reasoning 长 {len(reasoning)} 字符，可能被 max_tokens 截断）" if reasoning else ""
        raise RuntimeError(
            f"{error_prefix}模型返回空 content{hint}：推理模型 reasoning 占用 token，"
            "请改用「Anthropic 兼容」接口格式（thinking disabled）或调大「最大token」。"
        )
    usage = data.get("usage") or {}
    snap = llm_usage.record(usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return content, snap
