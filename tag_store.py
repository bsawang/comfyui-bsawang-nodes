# -*- coding: utf-8 -*-
"""tag 库持久层 + HTTP 路由：全项目 tag 系统的真相源。

管理 dict.json（基座字典）+ baskets/*.json（一个篮子一个文件）+ presets/*.json（预设），
被「提示词增强器 / Tag 反推 / Tag 库编辑」三个节点共用。本模块独立于各节点，
节点通过 import 本模块拿 tag 数据，不直接碰文件。

setup_routes(server)：注册 /bsawang/tag/* 与 /bsawang/prompt_enhancer/dict 路由（Tag 管理器 + 增强器刷新字典）。
"""
import json
from pathlib import Path

NODE_DIR = Path(__file__).parent
DICT_PATH = NODE_DIR / "dict.json"
BASKETS_DIR = NODE_DIR / "baskets"
PRESETS_DIR = NODE_DIR / "presets"

# tag 库版本号：任何篮子/预设写入时递增；并用 WebSocket 推送「库变了」事件（前端通知机制，非轮询）
TAG_LIB_VERSION = 0


def _bump_lib_version():
    global TAG_LIB_VERSION
    TAG_LIB_VERSION += 1
    try:
        from server import PromptServer
        PromptServer.instance.send_sync("bsawang/tag_lib_changed", {"version": TAG_LIB_VERSION})
    except Exception:
        pass


def _load_dict() -> dict:
    """读取外部字典 dict.json，并合并 baskets/ 目录下所有篮子文件（一个篮子一个文件）。
    缺失/损坏时显式报错（透传 UI），不静默回落。"""
    try:
        data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"[Tag] 字典文件读取失败：{DICT_PATH}（{e}）")
    if not data or not data.get("sections"):
        raise RuntimeError(f"[Tag] 字典文件为空或结构缺失：{DICT_PATH}")
    _merge_baskets(data)
    return data


def _merge_baskets(data: dict) -> None:
    """把 baskets/*.json（一个篮子一个文件）合并进 data['sections']。

    按 section.id 定位段（不存在自动新建），fields 按 key 去重追加，再按 order 排序。
    本地存在的篮子文件才会被合并——增删篮子 = 增删文件。
    """
    sec_index = {s["id"]: i for i, s in enumerate(data["sections"])}
    for fp in sorted(BASKETS_DIR.glob("*.json")):
        try:
            f = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"[Tag] 篮子文件读取失败：{fp}（{e}）")
        if not isinstance(f, dict):
            raise RuntimeError(f"[Tag] 篮子文件不是 JSON 对象：{fp}")
        key = f.get("key")
        section_id = f.get("section")
        if not key or not section_id:
            raise RuntimeError(f"[Tag] 篮子文件缺少 key/section：{fp}")

        # 定位或自动新建 section
        if section_id not in sec_index:
            data["sections"].append({
                "id": section_id,
                "title": f.get("section_title") or section_id,
                "fields": [],
            })
            sec_index[section_id] = len(data["sections"]) - 1
        sec = data["sections"][sec_index[section_id]]

        # section_title 一致性校验（提供了且与现有不同则报错）
        st = f.get("section_title")
        if st and sec.get("title") and sec["title"] != st and sec["title"] != section_id:
            raise RuntimeError(
                f"[Tag] section「{section_id}」标题不一致：{sec['title']} vs {st}（{fp}）"
            )
        if st and not sec.get("title"):
            sec["title"] = st

        # fields 按 key 去重
        fields = sec.setdefault("fields", [])
        for existing in fields:
            if existing.get("key") == key:
                raise RuntimeError(f"[Tag] 篮子 key 冲突：{key}（{fp}）")

        # 去掉 loader 元数据字段，其余进字段定义
        field = {k: v for k, v in f.items() if k not in ("section", "section_title")}
        field.setdefault("type", "basket")
        field.setdefault("default", "")
        fields.append(field)

    # 同 section 内：基座字段（无 order）保持 dict.json 原顺序在前，篮子字段按 order 排序追加在后
    for sec in data["sections"]:
        base = [f for f in sec["fields"] if "order" not in f]
        baskets = sorted(
            (f for f in sec["fields"] if "order" in f),
            key=lambda f: (f.get("order", 0), f.get("key", "")),
        )
        sec["fields"] = base + baskets


# 字典 mtime 缓存：dict.json + baskets/*.json 变化时自动重读（配合前端「刷新字典」按钮，无需重启）
_DICT_CACHE = {"stamp": None, "data": None}


def _dict_stamp():
    """返回 dict.json + baskets/*.json 的 (路径, mtime) 指纹，用于判断文件是否变化。"""
    stamps = [(str(DICT_PATH), DICT_PATH.stat().st_mtime)]
    for fp in sorted(BASKETS_DIR.glob("*.json")):
        stamps.append((str(fp), fp.stat().st_mtime))
    return tuple(stamps)


def _get_dict() -> dict:
    """读取字典（mtime 缓存）：文件没变返回缓存，变了才重新加载。"""
    try:
        stamp = _dict_stamp()
    except OSError:
        stamp = None
    if _DICT_CACHE["data"] is None or _DICT_CACHE["stamp"] != stamp:
        _DICT_CACHE["data"] = _load_dict()
        _DICT_CACHE["stamp"] = stamp
    return _DICT_CACHE["data"]


def _tasks_from_condition(condition):
    """从篮子 condition 推导适用任务类型（前端 tab 显隐 + 刷新接口共用）；无 condition = 全部任务。"""
    if not condition or not isinstance(condition, dict):
        return []
    return condition.get("任务类型", [])


DICT = _get_dict()


# ---------- 预设（presets/*.json） ----------

_PRESET_CACHE = {"stamp": None, "data": None}


def _presets_stamp():
    if not PRESETS_DIR.is_dir():
        return None
    stamps = []
    for p in sorted(PRESETS_DIR.glob("*.json")):
        stamps.append((p.name, p.stat().st_mtime_ns))
    return tuple(stamps)


def _load_presets() -> dict:
    """读取 presets/ 目录全部预设（mtime 缓存）：{key: {key,guidance,tags}}。"""
    try:
        stamp = _presets_stamp()
    except OSError:
        stamp = None
    if _PRESET_CACHE["data"] is None or _PRESET_CACHE["stamp"] != stamp:
        presets = {}
        if PRESETS_DIR.is_dir():
            for p in sorted(PRESETS_DIR.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                key = (d.get("key") or "").strip() or p.stem
                presets[key] = {
                    "key": key,
                    "guidance": d.get("guidance", ""),
                    "tags": d.get("tags", {}) or {},
                }
        _PRESET_CACHE["data"] = presets
        _PRESET_CACHE["stamp"] = stamp
    return _PRESET_CACHE["data"]


def _save_preset(name, tags, guidance="", create_only=False):
    """创建或更新预设。create_only=True 且已存在 → 报错（新增去重）；写 presets/{name}.json。返回安全 key。"""
    name = (name or "").strip().replace("/", "_").replace("\\", "_")
    if not name:
        raise ValueError("[Tag] 预设名不能为空")
    path = PRESETS_DIR / f"{name}.json"
    if path.exists() and create_only:
        raise ValueError(f"[Tag] 预设「{name}」已存在，请勿重复新增")
    # 自检：篮子 key 存在 + 组合约束（互斥/冲突/跨字段 gate）——坏预设任何入口都存不进去
    for bk in (tags or {}):
        if _basket_options(bk) is None:
            raise ValueError(f"[Tag] 预设的篮子「{bk}」不存在当前字典，请检查篮子 key")
    errs = _collect_basket_errors(tags or {})
    if errs:
        raise ValueError("[Tag] 预设组合自检失败：\n" + "\n".join("  - " + e for e in errs))
    data = {
        "key": name,
        "guidance": guidance or "",
        "tags": tags or {},
    }
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _PRESET_CACHE["data"] = None
    _bump_lib_version()
    return name


def _delete_preset(name):
    name = (name or "").strip().replace("/", "_").replace("\\", "_")
    path = PRESETS_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
    _PRESET_CACHE["data"] = None
    _bump_lib_version()
    return True


def _rename_preset(old_name, new_name, tags, guidance=""):
    """重命名预设：key+文件名一起改（key=name=文件名单一模型）。"""
    old_name = (old_name or "").strip().replace("/", "_").replace("\\", "_")
    new_name = (new_name or "").strip().replace("/", "_").replace("\\", "_")
    if not old_name or not new_name:
        raise ValueError("[Tag] 预设名不能为空")
    if old_name == new_name:
        raise ValueError("[Tag] 新旧预设名相同，无需重命名")
    old_path = PRESETS_DIR / f"{old_name}.json"
    if not old_path.exists():
        raise ValueError(f"[Tag] 预设文件不存在：{old_name}")
    new_path = PRESETS_DIR / f"{new_name}.json"
    if new_path.exists():
        raise ValueError(f"[Tag] 预设「{new_name}」已存在，无法重命名")
    data = {"key": new_name, "guidance": guidance or "", "tags": tags or {}}
    new_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    old_path.unlink()
    _PRESET_CACHE["data"] = None
    _bump_lib_version()
    return new_name


# ---------- 篮子（baskets/*.json） ----------


def _read_basket_file(basket):
    """读 baskets/{basket}.json 原始文件（key 已与文件名一致）；不存在报错。"""
    path = BASKETS_DIR / f"{basket}.json"
    if not path.exists():
        raise ValueError(f"[Tag] 篮子文件不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _write_basket_file(basket, data):
    """写回 baskets/{basket}.json + 失效字典缓存 + 递增库版本。"""
    (BASKETS_DIR / f"{basket}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _DICT_CACHE["data"] = None
    _bump_lib_version()


def _save_basket(key, guidance=None, create_only=False):
    """创建或更新篮子 meta。create_only=True 且 key 已存在 → 报错（新增去重）；不存在 → 新建（content section，空 options）。"""
    key = (key or "").strip()
    if not key:
        raise ValueError("[Tag] 篮子 key 不能为空")
    path = BASKETS_DIR / f"{key}.json"
    if path.exists():
        if create_only:
            raise ValueError(f"[Tag] 篮子「{key}」已存在，请勿重复新增")
        bf = json.loads(path.read_text(encoding="utf-8"))
    else:
        bf = {
            "key": key, "section": "content", "type": "basket",
            "options": [], "guidance": [], "option_guidance": {},
        }
    if guidance is not None:
        g = (guidance or "").strip()
        bf["guidance"] = [g] if g else []
    _write_basket_file(key, bf)
    return key


def _rename_basket(old_key, new_key, guidance=None):
    """重命名篮子：key+文件名一起改（key=label 单一模型），并迁移预设引用。"""
    old_key = (old_key or "").strip()
    new_key = (new_key or "").strip()
    if not old_key or not new_key:
        raise ValueError("[Tag] 篮子 key 不能为空")
    if old_key == new_key:
        raise ValueError("[Tag] 新旧 key 相同，无需重命名")
    old_path = BASKETS_DIR / f"{old_key}.json"
    if not old_path.exists():
        raise ValueError(f"[Tag] 篮子文件不存在：{old_key}")
    new_path = BASKETS_DIR / f"{new_key}.json"
    if new_path.exists():
        raise ValueError(f"[Tag] 篮子「{new_key}」已存在，无法重命名")
    bf = json.loads(old_path.read_text(encoding="utf-8"))
    bf["key"] = new_key
    if guidance is not None:
        g = (guidance or "").strip()
        bf["guidance"] = [g] if g else []
    new_path.write_text(json.dumps(bf, ensure_ascii=False, indent=2), encoding="utf-8")
    old_path.unlink()
    # 迁移预设引用：presets/*.json 的 tags 里旧 key → 新 key
    if PRESETS_DIR.is_dir():
        for p in sorted(PRESETS_DIR.glob("*.json")):
            try:
                pf = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            tags = pf.get("tags") or {}
            if old_key in tags:
                tags[new_key] = tags.pop(old_key)
                p.write_text(json.dumps(pf, ensure_ascii=False, indent=2), encoding="utf-8")
    _DICT_CACHE["data"] = None
    _bump_lib_version()
    return new_key


def _delete_basket(key):
    """删 baskets/{key}.json。"""
    key = (key or "").strip()
    path = BASKETS_DIR / f"{key}.json"
    if path.exists():
        path.unlink()
    _DICT_CACHE["data"] = None
    _bump_lib_version()
    return True


def _save_option(basket, tag, guidance=None):
    """在篮子 options 加/改一个 tag；guidance 写入 option_guidance[tag]（空则移除）。"""
    basket = (basket or "").strip()
    tag = (tag or "").strip()
    if not basket or not tag:
        raise ValueError("[Tag] 篮子/tag 不能为空")
    bf = _read_basket_file(basket)
    opts = bf.get("options", [])
    if tag not in opts:
        opts.append(tag)
        bf["options"] = opts
    og = bf.setdefault("option_guidance", {})
    g = (guidance or "").strip()
    if g:
        og[tag] = g
    else:
        og.pop(tag, None)
    _write_basket_file(basket, bf)
    return True


def _delete_option(basket, tag):
    """从篮子 options 移除 tag + 对应 option_guidance。"""
    basket = (basket or "").strip()
    tag = (tag or "").strip()
    if not basket:
        raise ValueError("[Tag] 篮子不能为空")
    bf = _read_basket_file(basket)
    bf["options"] = [o for o in bf.get("options", []) if o != tag]
    bf.get("option_guidance", {}).pop(tag, None)
    _write_basket_file(basket, bf)
    return True


def _add_basket_option(basket, tag):
    """往篮子 options 加 tag（去重）；basket 必须是已加载篮子（Tag_Reverse 采纳用）。"""
    _save_option(basket, tag)
    return True


def _basket_options(basket):
    """取某篮子已加载 options；不存在返回 None。"""
    for section in _get_dict()["sections"]:
        for f in section["fields"]:
            if f.get("type") == "basket" and f.get("key") == basket:
                return f.get("options", [])
    return None


def _collect_basket_errors(tags_by_field: dict) -> list:
    """收集内容层组合自检错误（互斥/冲突/跨字段矛盾）；无错返回空列表。

    tags_by_field: {字段key: [已选tag列表]}
    """
    errors = []
    for key, tags in tags_by_field.items():
        if not tags:
            continue
        # 1. 整栏互斥：一次只能选 1 个
        fld_mutex = None
        fld_conflicts = []
        for section in _get_dict()["sections"]:
            for fld in section["fields"]:
                if fld["key"] == key:
                    fld_mutex = fld.get("mutually_exclusive")
                    fld_conflicts = fld.get("conflicts", [])
                    break
        if fld_mutex and len(tags) > 1:
            errors.append(f"[{key}] 互斥：一次只能选一个，当前选了 {len(tags)} 个：{'、'.join(tags)}")
        # 2. 特定冲突对：同一栏内两个冲突 tag 同时出现
        for pair in fld_conflicts:
            if pair[0] in tags and pair[1] in tags:
                errors.append(f"[{key}] 冲突：『{pair[0]}』与『{pair[1]}』不能同时选")
    # 3. 跨字段矛盾：被 gate 的字段有值但 gate 字段未选（gate 关系由篮子文件定义）
    gate_map = {}
    for section in _get_dict()["sections"]:
        for f in section["fields"]:
            g = f.get("gate")
            if g:
                gate_map.setdefault(g, []).append(f["key"])
    for gate_key, gated_keys in gate_map.items():
        if not tags_by_field.get(gate_key):
            for k in gated_keys:
                if tags_by_field.get(k):
                    errors.append(
                        f"[跨字段] 矛盾：{gate_key} 未选，但「{k}」仍有选择（{'、'.join(tags_by_field[k])}），请先选 {gate_key} 或清空「{k}」"
                    )
    return errors


# ---------- HTTP 路由（Tag 管理器 + 增强器刷新字典共用） ----------


def setup_routes(server):
    """注册字典/预设/Tag 管理路由：增强器「刷新字典」+ Tag 管理器前后端。"""
    from aiohttp import web

    async def handle_dict(request):
        data = _get_dict()
        basket_meta = {}
        for section in data["sections"]:
            for field in section["fields"]:
                if field.get("type") == "basket":
                    basket_meta[field["key"]] = {
                        "options": field.get("options", []),
                        "mutually_exclusive": field.get("mutually_exclusive", False),
                        "conflicts": field.get("conflicts", []),
                        "condition": field.get("condition"),
                        "tasks": _tasks_from_condition(field.get("condition")),
                    }
        return web.json_response({"basket_meta": basket_meta, "presets": _load_presets()})

    server.routes.get("/bsawang/prompt_enhancer/dict")(handle_dict)

    # ---- Tag 管理器后端：库编辑 / Tag 反推 共用 ----

    async def handle_tag_dict(request):
        """GET /bsawang/tag/dict：全部篮子（含 guidance/option_guidance，供库编辑器）+ 预设池。"""
        data = _get_dict()
        baskets = {}
        for section in data["sections"]:
            for field in section["fields"]:
                if field.get("type") == "basket":
                    baskets[field["key"]] = {
                        "options": field.get("options", []),
                        "guidance": field.get("guidance", []),
                        "option_guidance": field.get("option_guidance", {}),
                        "mutually_exclusive": field.get("mutually_exclusive", False),
                        "conflicts": field.get("conflicts", []),
                        "condition": field.get("condition"),
                        "tasks": _tasks_from_condition(field.get("condition")),
                    }
        return web.json_response({"baskets": baskets, "presets": _load_presets()})

    async def handle_save_preset(request):
        try:
            body = await request.json()
            name = _save_preset(body.get("name"), body.get("tags") or {}, body.get("guidance", ""), body.get("create"))
            return web.json_response({"ok": True, "key": name})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_rename_preset(request):
        try:
            body = await request.json()
            new_name = _rename_preset(body.get("old_name"), body.get("new_name"), body.get("tags") or {}, body.get("guidance", ""))
            return web.json_response({"ok": True, "key": new_name})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_delete_preset(request):
        try:
            body = await request.json()
            _delete_preset(body.get("name"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_save_basket(request):
        try:
            body = await request.json()
            key = _save_basket(body.get("key"), body.get("guidance"), body.get("create"))
            return web.json_response({"ok": True, "key": key})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_rename_basket(request):
        try:
            body = await request.json()
            new_key = _rename_basket(body.get("old_key"), body.get("new_key"), body.get("guidance"))
            return web.json_response({"ok": True, "key": new_key})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_delete_basket(request):
        try:
            body = await request.json()
            _delete_basket(body.get("key"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_save_option(request):
        try:
            body = await request.json()
            _save_option(body.get("basket"), body.get("tag"), body.get("guidance"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_delete_option(request):
        try:
            body = await request.json()
            _delete_option(body.get("basket"), body.get("tag"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    async def handle_add_basket_option(request):
        try:
            body = await request.json()
            _add_basket_option(body.get("basket"), body.get("tag"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    server.routes.get("/bsawang/tag/dict")(handle_tag_dict)
    server.routes.post("/bsawang/tag/save_preset")(handle_save_preset)
    server.routes.post("/bsawang/tag/rename_preset")(handle_rename_preset)
    server.routes.post("/bsawang/tag/delete_preset")(handle_delete_preset)
    server.routes.post("/bsawang/tag/save_basket")(handle_save_basket)
    server.routes.post("/bsawang/tag/rename_basket")(handle_rename_basket)
    server.routes.post("/bsawang/tag/delete_basket")(handle_delete_basket)
    server.routes.post("/bsawang/tag/save_option")(handle_save_option)
    server.routes.post("/bsawang/tag/delete_option")(handle_delete_option)
    server.routes.post("/bsawang/tag/add_basket_option")(handle_add_basket_option)
