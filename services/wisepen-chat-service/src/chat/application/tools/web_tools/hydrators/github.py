from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from github import Github, GithubException, RateLimitExceededException, UnknownObjectException
from github.Repository import Repository

from .models import HydratedGitHubRepository, HydrationStatus


@dataclass(frozen=True, slots=True)
class GitHubRepositoryLocator:
    """GitHub 仓库定位信息。"""

    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


class GitHubHydrator:
    """基于 PyGithub 的内部 GitHub 仓库补全服务。"""

    __slots__ = ("_github",)

    def __init__(
            self,
            *,
            github_client: Github,
    ) -> None:
        self._github = github_client

    def hydrate(
            self,
            *,
            url: str | None = None,
            owner: str | None = None,
            repo: str | None = None,
    ) -> HydratedGitHubRepository:
        # 1. 解析/定位仓库
        locator = parse_github_repository(url=url, owner=owner, repo=repo)
        if locator is None:
            return HydratedGitHubRepository(
                status=HydrationStatus.NOT_FOUND,
                failure_reason="missing_or_invalid_repo"
            )

        # 2. 调用 API 并处理异常
        try:
            repository = self._github.get_repo(locator.full_name)
            return _repository_to_hydrated(repository)

        except UnknownObjectException:
            return HydratedGitHubRepository(
                status=HydrationStatus.NOT_FOUND,
                owner=locator.owner,
                name=locator.repo,
                full_name=locator.full_name,
                failure_reason="github_repo_not_found",
            )

        except RateLimitExceededException:
            return HydratedGitHubRepository(
                status=HydrationStatus.FAILED,
                owner=locator.owner,
                name=locator.repo,
                full_name=locator.full_name,
                failure_reason="github_rate_limited",
            )

        except GithubException as exc:
            return HydratedGitHubRepository(
                status=HydrationStatus.FAILED,
                owner=locator.owner,
                name=locator.repo,
                full_name=locator.full_name,
                failure_reason=f"github_http_{exc.status}",
            )


def parse_github_repository(
        *,
        url: str | None = None,
        owner: str | None = None,
        repo: str | None = None,
) -> GitHubRepositoryLocator | None:
    # 1. 优先尝试直接传入的 owner 和 repo
    clean_owner = owner.strip().strip("/") if owner is not None else None
    clean_repo = repo.strip().strip("/") if repo is not None else None

    if clean_repo and clean_repo.endswith(".git"):
        clean_repo = clean_repo[:-4]

    if clean_owner and clean_repo:
        return GitHubRepositoryLocator(owner=clean_owner, repo=clean_repo)

    if not url:
        return None

    # 2. 补全并解析 URL
    parsed = urlparse(url.strip())
    if not parsed.netloc and parsed.path.startswith("github.com/"):
        parsed = urlparse(f"https://{url.strip()}")

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    if host != "github.com":
        return None

    # 3. 提取路径中的关键信息
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None

    repo_name = parts[1][:-4] if parts[1].endswith(".git") else parts[1]

    return GitHubRepositoryLocator(owner=parts[0], repo=repo_name)


def _repository_to_hydrated(repository: Repository) -> HydratedGitHubRepository:
    # 解析许可证
    license_obj = repository.license
    license_name = (
        None if license_obj is None
        else (license_obj.spdx_id or license_obj.key or license_obj.name)
    )

    return HydratedGitHubRepository(
        status=HydrationStatus.HYDRATED,
        full_name=repository.full_name,
        owner=repository.owner.login,
        name=repository.name,
        description=repository.description,
        html_url=repository.html_url,
        homepage=repository.homepage,
        default_branch=repository.default_branch,
        language=repository.language,
        topics=tuple(repository.get_topics()),
        license=license_name,
        stars=repository.stargazers_count,
        forks=repository.forks_count,
        open_issues=repository.open_issues_count,
        pushed_at=repository.pushed_at.isoformat() if repository.pushed_at else None,
        updated_at=repository.updated_at.isoformat() if repository.updated_at else None,
    )
