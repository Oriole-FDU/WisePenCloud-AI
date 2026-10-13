# 目录、标题邻域与章节原文读取

[DocumentReading](../src/rag/application/reading.py) 只注入 ResourceSnapshotReader，通过当前已授权 Document 结构读取目录及精确原文。section_id、范围及原文语义见[模型](models.md)，请求快照见[授权用例](usecases-access.md)。

## 1. 最小调用与参数

```python
from rag.application.reading import SectionReadScope


async def read_after_search(reading, snapshot, hit):
    outline = await reading.global_outline(hit.resource_id, snapshot=snapshot)
    ids = [hit.section_id] if hit.section_id is not None else []
    neighborhood = await reading.neighborhood(ids, snapshot=snapshot)
    text = await reading.read_sections(
        ids, snapshot=snapshot, scope=SectionReadScope.SUBTREE,
    )
    return outline, neighborhood, text
```

| 方法 | 参数和默认值 | 输出 |
|---|---|---|
| global_outline | resource_id；snapshot 必填；max_level=0（不限层级） | DocumentOutline 或 None |
| neighborhood | section_ids；snapshot 必填；sibling_steps=1 | list[SectionOutline] |
| read_sections | section_ids；snapshot 必填；scope=OWN | list[SectionText] |

max_level/sibling_steps 必须非负，scope 只允许 own/subtree，错误在 I/O 前失败。列表去重保序；相同调用中同一 Document 的 formatter 可复用。

## 2. 输出与范围

DocumentOutline 为 resource_id/content_revision/outline；SectionOutline 再有 section_id/section_path；SectionText 使用 text 代替 outline。section_path 为字符串列表，章节 ID 可继续传入本用例。

目录复用 Common OutlineFormatter，保留 ID、offset、anchors、当前标记、summary、preview。它不同于 Contextualizer 的纯标题材料大纲。

OWN 直接切 Document.text 的 own_span，SUBTREE 直接切 subtree_span；保留标题、中文、空白和 Markdown，不拼 Chunk、不重建标题。不提供正文窗口、续读游标或递归深度；大范围材料预算由调用方管理。

## 3. 注意事项与失败

调用方直接使用返回 section_id，不解析、不另外附加资源/revision。Mongo 章节索引定位后由 SnapshotReader 核对当前版本、权限与完整正文身份；旧版本章节不会指向新版，无旧 ID 兼容或自动存量迁移。

目录不可见返回 None，可见但无章节时 outline 为 `""`；批量章节缺失/不可见省略。存储或解码错误传播；原文范围非法抛 RuntimeError，不悄悄裁剪。一次上层“检索→目录→原文”应共享快照，新请求重新观察版本/ACL。

验证入口：[test_structured_reading.py](../tests/application/test_structured_reading.py)。
