import pytest

from common.core.exceptions import RpcError, ServiceException

from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import RagServiceClient


class _RpcClient:
    def __init__(self, result: object | BaseException) -> None:
        self._result = result

    async def post(self, *args, **kwargs) -> object:
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rpc_code", "expected_code"),
    (
        (42001, McpErrorCode.RAG_NAVIGATION_INVALID.code),
        (42002, McpErrorCode.RAG_NAVIGATION_STATE_NOT_FOUND.code),
        (42003, McpErrorCode.RAG_NAVIGATION_STATE_INVALIDATED.code),
        (None, McpErrorCode.RAG_NAVIGATION_FAILED.code),
    ),
)
async def test_client_maps_rag_rpc_errors(rpc_code: int | None, expected_code: int) -> None:
    client = RagServiceClient(
        _RpcClient(
            RpcError(
                "wisepen-rag-service",
                "/internal/rag/knowledge-navigation/locate",
                code=rpc_code,
                msg="rag error",
            )
        )
    )

    with pytest.raises(ServiceException) as error:
        await client.locate(session_id="session-1", query="query", max_results=1)

    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_client_rejects_non_mapping_response() -> None:
    client = RagServiceClient(_RpcClient([]))

    with pytest.raises(ServiceException) as error:
        await client.locate(session_id="session-1", query="query", max_results=1)

    assert error.value.code == McpErrorCode.RAG_NAVIGATION_FAILED.code
