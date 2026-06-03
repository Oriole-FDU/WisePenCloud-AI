class SkillBundleBuildError(Exception):
    """
    Skill Bundle 构建失败基类。

    - 用于包住 create bundle 过程中的非预期异常。
    - tool 层可以统一捕获这个基类并返回 Tool Error。
    """


class SkillBundleMarkdownRenderError(SkillBundleBuildError):
    """
    SKILL.md 渲染失败。

    - frontmatter 渲染失败。
    - Markdown body 渲染失败。
    - Markdown 解析 smoke check 失败。
    """


class SkillBundlePathError(SkillBundleBuildError):
    """
    Bundle 内部路径解析失败。

    - relative_path 为空。
    - relative_path 使用非 POSIX 分隔符。
    - relative_path 是绝对路径。
    - relative_path 包含 '..'。
    - relative_path 逃逸 bundle 根目录。
    """
