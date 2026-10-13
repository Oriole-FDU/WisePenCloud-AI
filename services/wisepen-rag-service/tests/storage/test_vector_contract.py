"""向量存储值对象的依赖边界：不加载驱动或运行配置。"""

import subprocess
import sys


def test_vector_storage_contract_has_no_backend_or_runtime_dependency():
    code = """
import sys
from rag.storage.vector import VectorRecord, VectorQuery, VectorMatch
from rag.core.schema.filters import MetadataFilter, MetadataFilters
assert VectorRecord.__module__ == 'rag.storage.vector'
assert VectorQuery.__module__ == 'rag.storage.vector'
assert VectorMatch.__module__ == 'rag.storage.vector'
assert not any(name.startswith(('qdrant_client', 'rag.backends',
    'rag.ingestion', 'rag.application', 'rag.core.config')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True)
