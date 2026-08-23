# -*- coding: utf-8 -*-
from .h3_api_prompt_formatter import (
    NODE_CLASS_MAPPINGS as H3_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as H3_DISPLAY,
)
from .llm_api_configurator import (
    NODE_CLASS_MAPPINGS as CONFIG_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as CONFIG_DISPLAY,
)
from .prompt_enhancer import (
    NODE_CLASS_MAPPINGS as ENHANCER_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as ENHANCER_DISPLAY,
    WEB_DIRECTORY,
)
from .tag_reverse import (
    NODE_CLASS_MAPPINGS as REV_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as REV_DISPLAY,
)
from .tag_library import (
    NODE_CLASS_MAPPINGS as LIB_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as LIB_DISPLAY,
)

NODE_CLASS_MAPPINGS = {**H3_MAPPINGS, **CONFIG_MAPPINGS, **ENHANCER_MAPPINGS, **REV_MAPPINGS, **LIB_MAPPINGS}
NODE_DISPLAY_NAME_MAPPINGS = {**H3_DISPLAY, **CONFIG_DISPLAY, **ENHANCER_DISPLAY, **REV_DISPLAY, **LIB_DISPLAY}

# 注册 API 路由：LLM token 统计 + tag 库（/bsawang/tag/* 与增强器字典刷新）
try:
    from . import llm_usage
    from . import tag_store
    from server import PromptServer
    llm_usage.setup_routes(PromptServer.instance)
    tag_store.setup_routes(PromptServer.instance)
except Exception:
    pass

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
