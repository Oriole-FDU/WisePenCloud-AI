"""按启动配置显式接入扩展；保留摄入、额外存储和 API 三类装配产物。"""

from collections.abc import Callable

from dependency_injector import containers

from .base import Extension
from .employment import EmploymentContainer

# 扩展名到容器工厂的注册表
_BUILDERS: dict[str, Callable[..., containers.DeclarativeContainer]] = {
    "employment": EmploymentContainer,
}


def register_extensions(
        platform: containers.DeclarativeContainer,
) -> dict[str, Extension]:
    """构造扩展并注册来源存储；由平台生命周期统一初始化和挂载。"""
    names = platform.settings().EXTENSIONS

    # 先校验扩展名，避免部分构造后再报错
    for name in names:
        if name not in _BUILDERS:
            raise ValueError(f"unknown extension: {name}")
    if not names:
        return {}

    registered = {}
    # 已由平台管理的向量集合，用于检测扩展冲突
    collections = {store.collection for store in platform.vector_storages()}

    for name in names:
        extension = _BUILDERS[name](platform=platform).extension()

        # 扩展新增的向量集合不能与已有集合重名
        for item in extension.vector_collections:
            store = item.storage
            if store.collection in collections:
                raise ValueError(
                    f"extension collection already managed: {store.collection}"
                )
            collections.add(store.collection)

        # 把扩展产物挂到平台：流水线路由、向量集合、向量存储
        platform.pipeline_routes()[name] = extension.pipeline
        platform.vector_collections().extend(extension.vector_collections)
        platform.vector_storages().extend(
            item.storage for item in extension.vector_collections
        )
        registered[name] = extension

    return registered
