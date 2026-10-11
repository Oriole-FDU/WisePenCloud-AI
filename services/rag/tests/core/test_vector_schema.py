"""共享向量模型的依赖边界：不因定义投影合同加载驱动或运行配置。"""

import subprocess
import sys


def test_schema_vector_import_has_no_backend_or_runtime_dependency():
    code = """
import sys
from rag.core.schema import VectorRecord, VectorQuery, VectorMatch
from rag.core.schema.filters import MetadataFilter, MetadataFilters
assert VectorRecord.__module__ == 'rag.core.schema.vector'
assert VectorQuery.__module__ == 'rag.core.schema.vector'
assert VectorMatch.__module__ == 'rag.core.schema.vector'
assert not any(name.startswith(('qdrant_client', 'rag.storage', 'rag.backends',
    'rag.ingestion', 'rag.application', 'rag.core.config')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True)
