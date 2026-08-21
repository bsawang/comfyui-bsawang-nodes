# -*- coding: utf-8 -*-
"""Tag 反推匹配节点：反推文字 → 结构化 tag 集合 + 扩展建议。

反推文字 → LLM（templates/Tag反推结构化.txt）对照已加载 tag 池 →
「已匹配 tag 集合」+「扩展建议」。双输出：tag集合JSON（matched，喂下游增强器/预设）
+ 反推结果（完整 matched+suggestions，供面板直出渲染）。
采纳/存预设由前端调 /bsawang/tag/* 路由写持久层。
中间节点，结果随工作流输出直出，后端不存反推状态。
"""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import llm_usage
from .prompt_enhancer import _basket_options, _get_dict

NODE_DIR = Path(__file__).parent
TEMPLATE_PATH = NODE_DIR / "templates" / "Tag反推结构化.txt"


def _read_system_prompt(path_str: str) -> str:
    """读 system prompt 文件；路径为空回落默认模板；给了但读不到显式报错。"""
    path_str = (path_str or "").strip()
    if not path_str:
        path_str = str(TEMPLATE_PATH)
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"[Tag反推] 系统提示词文件读取失败：{path_str}（{e}）")


def _post_json(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"[Tag反推] HTTP {e.code}：{body[:500]}")
    except Exception as e:
        raise RuntimeError(f"[Tag反推] 请求失败：{e}")


def _call_llm(config, system, user_msg) -> str:
    """LLM 调用（Anthropic 兼容 / OpenAI 兼容），返回纯文本 content。"""
    interface = config.get("接口格式") or "Anthropic 兼容"
    model = config.get("模型") or "deepseek-v4-flash"
    base_url = (config.get("API基础URL") or "").strip().rstrip("/")
    api_key_env = (config.get("APIKey环境变量") or "ANTHROPIC_AUTH_TOKEN").strip()
    temperature = config.get("温度", 0.4)
    max_tokens = int(config.get("最大token", 8192))
    api_key = os.environ.get(api_key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise ValueError(f"[Tag反推] 未找到 API key：环境变量「{api_key_env}」为空。")

    if interface.startswith("Anthropic"):
        url = base_url + "/v1/messages" if not base_url.endswith("/v1/messages") else base_url
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": user_msg}],
            "temperature": temperature,
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        data = _post_json(url, headers, payload)
        content = "".join(
            c.get("text", "")
            for c in data.get("content", [])
            if isinstance(c, dict) and c.get("type") == "text"
        )
        usage = data.get("usage") or {}
        llm_usage.record(usage.get("input_tokens"), usage.get("output_tokens"))
    else:
        url = base_url + "/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        data = _post_json(url, headers, payload)
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"[Tag反推] 响应结构异常：{json.dumps(data, ensure_ascii=False)[:500]}")
        usage = data.get("usage") or {}
        llm_usage.record(usage.get("prompt_tokens"), usage.get("completion_tokens"))

    if not content.strip():
        raise RuntimeError("[Tag反推] LLM 返回空内容。请检查模型/接口配置，或调大「最大token」。")
    return content.strip()


def _parse_json(content) -> dict:
    """从 LLM 输出提取 JSON（容忍 markdown 代码围栏/前后缀）。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        raise RuntimeError(f"[Tag反推] LLM 输出不含 JSON：{content[:300]}")
    try:
        return json.loads(text[s : e + 1])
    except Exception:
        raise RuntimeError(f"[Tag反推] JSON 解析失败：{text[s : e + 1][:300]}")


def _run_match(config, text, sp_file):
    """执行匹配：反推文字 → 结构化 matched + suggestions。返回 (matched, suggestions)。"""
    pool_rows = []
    for section in _get_dict()["sections"]:
        for f in section["fields"]:
            if f.get("type") == "basket":
                pool_rows.append(f"{f['key']}：{'、'.join(f.get('options', []))}")
    pool_block = "\n".join(pool_rows)

    system = _read_system_prompt(sp_file)
    user_msg = f"【反推文字】\n{text}\n\n【已加载tag】\n{pool_block}"
    # 匹配是分类任务：强制低温（0.0）减少飘逸，结果更稳定
    content = _call_llm(_stable_config(config, 0.0), system, user_msg)
    parsed = _parse_json(content)

    # 校验：matched 只保留池内存在的 tag；suggestions 的篮子必须在池内
    valid_matched = {}
    for basket, tags in (parsed.get("matched") or {}).items():
        opts = _basket_options(basket)
        if opts is None:
            continue
        keep = [t for t in tags if t in opts]
        if keep:
            valid_matched[basket] = keep
    clean_suggestions = []
    for s in parsed.get("suggestions") or []:
        if not isinstance(s, dict):
            continue
        bk = (s.get("篮子") or "").strip()
        tag = (s.get("tag") or "").strip()
        opts = _basket_options(bk)
        # 建议必须是「库中不存在」的新 tag：库中已有的（如 跪趴撅臀/私处/ahegao）过滤掉
        if bk and tag and opts is not None and tag not in opts:
            clean_suggestions.append({"篮子": bk, "tag": tag, "reason": (s.get("reason") or "").strip()})
    return valid_matched, clean_suggestions


def _stable_config(config, temp):
    """复制配置并覆盖温度（匹配/归纳用低温，减少飘逸）。"""
    c = dict(config)
    c["温度"] = temp
    return c


def _generate_guidance(config, text):
    """独立调用：把反推文字归纳成一段预设引导词（纯文本输出，无 JSON 解析风险）。"""
    system = (
        "你是 AI 图像生成场景归纳助手。把用户给的场景描述归纳成一段预设引导词（1-2 句），"
        "概括核心场景：主体/环境/构图/光线/氛围/风格。直接输出引导词本身，中文，"
        "不要任何前缀、解释、markdown 或引号包裹。"
    )
    user_msg = f"【场景描述】\n{text}"
    content = _call_llm(_stable_config(config, 0.2), system, user_msg)
    return content.strip().strip('"').strip("'")


class Tag_Reverse:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "反推文字": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "placeholder": "VL 反推输出 / 多维描述（如 AILab_QwenVL 反推结果）",
                    },
                ),
            },
            "optional": {
                "LLM": ("LLM_CONFIG", {}),
                # 系统提示词文件：原生 widget，面板创建后重排到最底部
                "系统提示词文件": (
                    "STRING",
                    {
                        "default": str(TEMPLATE_PATH),
                        "tooltip": "反推匹配 system prompt（结构化 JSON 模板），改 txt 即生效",
                    },
                ),
                # 当前 tag 集合（matched，随工作流序列化；面板写，反推结果覆盖）
                "bsawang_tag_state": (
                    "STRING",
                    {"default": "{}", "multiline": True, "hidden": True},
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("tag集合JSON", "反推结果")
    FUNCTION = "process"
    CATEGORY = "bsawang/提示词增强器"

    def process(self, 反推文字, 系统提示词文件, LLM=None, **kw):
        if not isinstance(LLM, dict) or not LLM.get("模型"):
            raise ValueError("[Tag反推] 需要接「LLM API 设定器」的输出。")
        text = (反推文字 or "").strip()
        if not text:
            raise ValueError("[Tag反推] 反推文字为空。")

        valid_matched, clean_suggestions = _run_match(LLM, text, 系统提示词文件)
        try:
            guidance = _generate_guidance(LLM, text)
        except Exception:
            guidance = ""  # 引导词归纳失败不阻塞主匹配
        full = {"matched": valid_matched, "suggestions": clean_suggestions, "引导词": guidance, "反推文字": text}
        return (
            json.dumps(valid_matched, ensure_ascii=False),
            json.dumps(full, ensure_ascii=False),
        )


NODE_CLASS_MAPPINGS = {"Tag_Reverse": Tag_Reverse}
NODE_DISPLAY_NAME_MAPPINGS = {"Tag_Reverse": "Tag 反推"}
