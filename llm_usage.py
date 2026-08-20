# -*- coding: utf-8 -*-
"""LLM token 用量统计（会话级，进程内累计）。

增强器 / H3 格式化调用 LLM 后把 usage 记进来；前端消费节点执行后
通过 GET /llm_usage/last 读取「本次」用量，在节点标题栏显示。

进程内全局变量，重启 ComfyUI 清空——符合「会话级累计」语义。
"""
import threading

_lock = threading.Lock()
_state = {
    "本次输入token": 0,
    "本次输出token": 0,
    "累计输入token": 0,
    "累计输出token": 0,
    "调用次数": 0,
}


def setup_routes(server):
    """注册 GET /llm_usage/last 路由：返回最近一次 LLM 调用的本次用量。"""
    from aiohttp import web

    async def handle_last(request):
        return web.json_response(snapshot())

    server.routes.get("/llm_usage/last")(handle_last)


def record(input_tokens: int, output_tokens: int) -> dict:
    """记录一次 LLM 调用消耗，返回当前会话快照（本次 + 累计）。"""
    global _state
    with _lock:
        _state["本次输入token"] = int(input_tokens or 0)
        _state["本次输出token"] = int(output_tokens or 0)
        _state["累计输入token"] += int(input_tokens or 0)
        _state["累计输出token"] += int(output_tokens or 0)
        _state["调用次数"] += 1
        return dict(_state)


def snapshot() -> dict:
    """读取当前会话统计快照。"""
    with _lock:
        return dict(_state)


def reset() -> dict:
    """清零统计（前端可调，重开会话用）。"""
    global _state
    with _lock:
        _state = {
            "本次输入token": 0,
            "本次输出token": 0,
            "累计输入token": 0,
            "累计输出token": 0,
            "调用次数": 0,
        }
        return dict(_state)


def format_snapshot(snap: dict = None) -> str:
    """格式化统计为可读字符串：如「本次↑1.2k/3k 累计 45.6k/89k·12次」。"""
    snap = snap or snapshot()

    def _fmt(n):
        if n >= 1000000:
            return f"{n/1000000:.1f}M"
        if n >= 1000:
            return f"{n/1000:.1f}k"
        return str(n)

    return (
        f"本次↑{_fmt(snap['本次输入token'])}/↓{_fmt(snap['本次输出token'])} "
        f"累计 {_fmt(snap['累计输入token'])}/↓{_fmt(snap['累计输出token'])}·{snap['调用次数']}次"
    )
