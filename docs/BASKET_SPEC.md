# 篮子制作规约（Basket Specification）

> 提示词增强器「内容设置」的 tag 篮子：**一个篮子 = 一个文件**。
> 节点加载时把 `baskets/` 目录下所有 JSON 文件**动态合并**进字典（DICT），增删篮子 = 增删文件，不改节点代码。

## 1. 目录与文件规则

| 项 | 规则 |
|---|---|
| 篮子目录 | `baskets/`（节点包根目录，与 dict.json 同级）|
| 文件粒度 | 一个篮子一个 JSON 文件 |
| 文件命名 | `{key}.json` |
| 部署目录 | 运行副本 `custom_nodes/ComfyUI-bsawang/baskets/`（与仓库同步）|

## 2. 文件格式

示例 `baskets/光线.json`：

```json
{
  "key": "光线",
  "label": "光线",
  "section": "content",
  "section_title": "内容设置",
  "type": "basket",
  "options": [
    "黄金时刻",
    "逆光",
    "顶光",
    "侧光",
    "伦勃朗光",
    "蝴蝶光",
    "硬光",
    "柔光",
    "霓虹光",
    "烛光",
    "晨光",
    "月光"
  ],
  "mutually_exclusive": false,
  "conflicts": [
    ["硬光", "柔光"],
    ["晨光", "月光"],
    ["烛光", "霓虹光"]
  ],
  "condition": {
    "任务类型": ["文生视频(T2V)", "图生视频(I2V)"]
  }
}
```

> 完整参考：`baskets/主题风格.json`（guidance 强约束 + option_guidance 题材词表）、`baskets/艺术风格.json`（同上）、本地补充篮子（guidance 维度规则 + gate 跨字段）。

### 字段表

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | 唯一键，全目录不重复；state 存储 key + 传 LLM 的维度名 |
| `label` | ✅ | 显示名（tab 内维度名 / 传给 LLM 的名称）|
| `section` | ✅ | 所属 tab 的 id。现有：`type` / `type_info` / `content` / `output`；**新 tab 用新 id，加载器自动创建** |
| `section_title` | | 新 tab 时的显示标题（不写则用 `section` 当标题）；同 `section` 的文件需保持一致 |
| `type` | ✅ | 固定 `"basket"` |
| `options` | ✅ | 可选 tag 数组，非空、不重复、无空串 |
| `mutually_exclusive` | | `true` = 单选篮子（一次只能选 1 个）|
| `conflicts` | | `[["A","B"]]` = A、B 不可同选（值必须在 options 内）|
| `condition` | | 仅这些任务类型时生效（**前端 tab 显隐 + 运行时是否传 LLM 都由它驱动**）；缺省 = 全部任务类型 |
| `order` | | 同 section 内排序（升序，缺省 0）；控制篮子在前端 tab 行的位置 |
| `gate` | | 跨字段前置：本篮子有值时必须先选 `gate` 指定的篮子 key（该 key 未选则报错）|
| `guidance` | | 系统提示词补充：本篮子被选中时注入 system prompt 的规则行（字符串数组，按选中篮子去重注入）|
| `option_guidance` | | 按选项注入：`{选项: 规则行}`，本篮子被选中时，只注入**已选选项**对应的行（主题风格/艺术风格特点词表用）|

### 注入机制（guidance / option_guidance）

系统提示词 = 基座 `prompt_enhancer_system.txt` + **运行时按选中篮子注入**：

- 篮子有选中 tag → 该篮子的 `guidance`（整篮规则）注入
- 选中 tag 里有 `option_guidance` 对应行 → **只注入已选选项**的行（未选的不给，省 token、不干扰）
- 多篮子/多行**去重**；未选中篮子不注入 → 安全内容不带无关规则

### 约束

- `mutually_exclusive: true` 时**不要**再配 `conflicts`（互斥已保证单值，conflicts 冗余）
- `condition.任务类型` 只能是四类之一：`文生图(T2I)` / `图生图(I2I)` / `文生视频(T2V)` / `图生视频(I2V)`
- 同一 `section` 的文件，`section_title` 必须一致
- 同 section 内按 `order` 升序排列（缺省 0）

## 3. 制作流程

1. **定内容**：确认篮子的 tag 维度与可选值（对齐描述圣经 / 知识库标准，如 `resources/prompts/标准/`）
2. **定归属**：选 `section`（现有 tab 直接引用 id；新 tab 用新 id + 补 `section_title`）
3. **写文件**：参考同 section 现有文件格式，填全必填字段
4. **质检**：按 §4 清单自检
5. **部署**：复制到部署目录 `baskets/`
6. **生效**：重启 ComfyUI（若热加载已实现则点「刷新字典」）

## 4. 质检清单

- [ ] `key` 全局唯一
- [ ] `options` 非空、无重复、无空串
- [ ] `conflicts` 所有 tag 都在 `options` 内
- [ ] `mutually_exclusive` 与 `conflicts` 不同时用
- [ ] `condition.任务类型` 合法
- [ ] 同 `section` 的文件 `section_title` 一致
- [ ] 文件名 = `{key}.json`
- [ ] `option_guidance` 的 key 都在 `options` 内
- [ ] `gate` 指向的篮子 key 存在
- [ ] 字段全部符合 §2 字段表

## 5. 节点加载逻辑（契约）

节点启动时：

```
DICT = 基座 dict.json（sections 骨架 + 非篮子控件）
for f in baskets/*.json:          # 本地存在的所有篮子文件
    DICT = merge(DICT, f)          # 按 section.id 定位段 → fields 按 key 去重追加
```

- `section` 不存在于基座 → **自动新建** tab（标题取 `section_title`，缺省用 section id）
- 目录里没有的文件 → 对应篮子不出现（增删篮子 = 增删文件）
- 同一 section 内 `key` 冲突 → 加载报错（防静默覆盖）

运行时（每次 enhance）：

```
system prompt = 基座 prompt_enhancer_system.txt
                 + 选中篮子的 guidance（去重）
                 + 选中选项的 option_guidance 行（去重）
```

- 未选中的篮子不注入 → 安全内容不带无关规则
- 注入规则只来自本地存在的篮子文件
