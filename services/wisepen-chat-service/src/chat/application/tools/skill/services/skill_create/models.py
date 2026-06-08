from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True, slots=True)
class SkillMarkdownExample:
    """
    Skill 使用示例。

    Args:
    - title: 示例标题。
    - user_input: 用户可能提出的请求。
    - expected_behavior: Skill 触发后应该怎么处理。
    """

    title: str
    user_input: str
    expected_behavior: str


@dataclass(frozen=True, slots=True)
class SkillWorkflowStep:
    """
    Skill 工作流步骤。

    Args:
    - step: 主步骤。
    - sub_steps: 可选子步骤。
    """

    step: str
    sub_steps: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillMarkdownDraft:
    """
    SKILL.md 正文结构化草稿。

    - description 不放在这里：
      - description 属于 frontmatter 触发语句。
      - purpose 属于正文执行意图。
    - service 负责把本结构渲染成统一风格的 Markdown body。

    Args:
    - purpose: 这个 Skill 解决什么任务。
    - workflow: 执行步骤。
    - output_requirements: 输出格式和质量要求。
    - input_requirements: 用户需要提供什么。
    - tool_guidance: 工具使用建议。
    - resource_guidance: 什么时候读取 references/assets/scripts。
    - constraints: 边界和注意事项。
    - examples: 输入/行为示例。
    """

    purpose: str
    workflow: List[SkillWorkflowStep]
    output_requirements: List[str]
    input_requirements: List[str] = field(default_factory=list)
    tool_guidance: List[str] = field(default_factory=list)
    resource_guidance: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    examples: List[SkillMarkdownExample] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillBundleReferenceDraft:
    """
    references/ 下的静态文档草稿。

    - references 是给模型按需读取的静态说明文档。
    - v1 只支持模型直接注入文本内容。

    Args:
    - relative_path: 相对于 references/ 的路径。
    - description: 文档用途说明。
    - content: 文档文本内容。
    """

    relative_path: str
    description: str
    content: str


@dataclass(frozen=True, slots=True)
class SkillBundleAssetDraft:
    """
    assets/ 下的资源草稿。

    - assets 是模板、示例、配置、schema、样式等任务资源。
    - v1 使用 content_text 表示模型直接注入的文本资源。
    - v2 可增加 file_ref，用于接入上传链路或二进制资源链路。

    Args:
    - relative_path: 相对于 assets/ 的路径。
    - description: 资源用途说明。
    - content_text: 文本资源内容。
    """

    relative_path: str
    description: str
    content_text: str


@dataclass(frozen=True, slots=True)
class SkillBundleScriptDraft:
    """
    scripts/ 下的脚本草稿。

    - create skill bundle 阶段只打包脚本，不执行脚本。
    - 脚本如何执行由后续 sandbox runtime 决定。

    Args:
    - relative_path: 相对于 scripts/ 的路径。
    - description: 脚本用途说明。
    - content: 脚本文本内容。
    """

    relative_path: str
    description: str
    content: str


@dataclass(frozen=True, slots=True)
class CreateSkillBundleRequest:
    """
    创建 Skill Bundle 的协议模型。

    - 只描述 Agent 注入的源材料。
    - 不描述发布态。
    - 不包含 object_key / assets_manifest / Mongo 字段。
    - 不包含 user_id / session_id / created_at 等可信审计字段；
      这些由 service 从上下文注入。

    Args:
    - skill_id: Skill 目录名。
    - display_name: 展示名。
    - description: frontmatter 触发描述。
    - markdown: SKILL.md 正文结构化草稿。
    - version: bundle 版本。
    - references: 写入 references/ 的文档。
    - assets: 写入 assets/ 的资源。
    - scripts: 写入 scripts/ 的脚本。
    """

    skill_id: str
    display_name: str
    description: str
    markdown: SkillMarkdownDraft
    version: str = "1.0.0"
    references: List[SkillBundleReferenceDraft] = field(default_factory=list)
    assets: List[SkillBundleAssetDraft] = field(default_factory=list)
    scripts: List[SkillBundleScriptDraft] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SkillBundleBuildContext:
    """
    服务端注入的构建上下文。

    - 这些字段来自可信上下文，不由模型注入。
    - service 用它们渲染 SKILL.md frontmatter.metadata。

    Args:
    - user_id: 当前用户 ID。
    - session_id: 当前会话 ID。
    """

    user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class SkillBundleFileSummary:
    """
    生成后的文件摘要。

    Args:
    - path: bundle 内部相对路径。
    - size_bytes: 写入后的字节数。
    - description: 文件说明。
    """

    path: str
    size_bytes: int
    description: str


@dataclass(frozen=True, slots=True)
class CreateSkillBundleResult:
    """
    Skill Bundle 构建结果。

    - 这里只表示 bundle 已生成。
    - 不表示 Skill 已安装、已发布或已启用。

    Args:
    - skill_id: Skill ID。
    - display_name: 展示名。
    - version: bundle 版本。
    - bundle_dir_ref: 生成后的 skill 目录路径引用。
    - files: bundle 内文件摘要。
    """

    skill_id: str
    display_name: str
    version: str
    bundle_dir_ref: str
    files: List[SkillBundleFileSummary]