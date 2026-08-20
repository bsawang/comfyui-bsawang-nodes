---
title: bsawang-nodes（提示词增强器插件）
category: node
topic: 提示词增强器
tags: [bsawang, 插件, 节点, 提示词]
model: 通用
scene: 提示词增强-节点插件
asset: 节点
reusable: true
related:
  - "[[resources/prompts/标准/人物描述标准]]"
  - "[[resources/prompts/标准/图片整体描述标准]]"
  - "[[resources/prompts/标准/视频描述标准]]"
  - "[[resources/prompts/标准/艺术风格词汇参考]]"
---

# bsawang-nodes（提示词增强器插件）

bsawang 提示词增强器 ComfyUI 插件（独立 GitHub 仓库 `bsawang/comfyui-bsawang-nodes`），含 3 个节点：

- **H3_API_PromptFormatter** — H3 视频提示词格式化（六段式/base 三字段）
- **LLM_API_Configurator** — LLM API 设定器（连接配置 → LLM_CONFIG）
- **Prompt_Enhancer** — 提示词增强器（四栏 UI + tag 篮子多选 + 约束自检 + 风格强约束）

完整文档见本目录 [README.md](README.md)（GitHub 仓库根）。运行副本：`H:\ComfyUI_Windows_portable\ComfyUI\custom_nodes\ComfyUI-bsawang\`。

> 本文件为 aigc-study 父项目图谱的锚点（目录级双链 `[[assets/nodes/bsawang-nodes/_index]]` 建边用）。
