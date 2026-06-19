# github_hydrate

实现入口：`src/chat/application/tools/web_tools/github_hydrate_tool.py`

`github_hydrate` 使用 PyGithub 补全 GitHub 仓库元数据。它只做结构化水合，不 clone 仓库，不抓 README，不读源码文件。

## 何时使用

- 已经有明确仓库信号，例如 GitHub URL 或 `owner/repo`。
- 需要补全 topics、license、default branch、stars、forks、issues 或更新时间等信息。
- 搜索结果已经明确是 GitHub 仓库，且结构化元数据会显著提升下一步判断。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `url` | `string` | 仓库 URL，支持 repo 根页、issue、pull、blob、tree、releases 等路径。 |
| `owner` | `string` | 仓库 owner。 |
| `repo` | `string` | 仓库名。 |

## 输出

返回 `HydratedGitHubRepository`：

- `status`
- `full_name`
- `owner`
- `name`
- `description`
- `html_url`
- `homepage`
- `default_branch`
- `language`
- `topics`
- `license`
- `stars`
- `forks`
- `open_issues`
- `pushed_at`
- `updated_at`

## 边界

- 优先使用 `owner/repo`，否则使用 URL。
- 不 clone 仓库。
- 不抓 README。
- 不读取源码文件。
- 仓库不存在返回 `not_found`。
- 额度或 API 异常返回 `failed`。
