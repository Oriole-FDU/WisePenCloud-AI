from __future__ import annotations

import ast
from pathlib import Path


CONTAINER_PATH = (
    Path(__file__).parents[1] / "src" / "chat" / "container.py"
)


def _container_assignment_keyword(assignment_name: str, keyword_name: str) -> str:
    tree = ast.parse(CONTAINER_PATH.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "Container":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == assignment_name
                for target in statement.targets
            ):
                continue
            if not isinstance(statement.value, ast.Call):
                raise AssertionError(f"{assignment_name} is not provider call")
            for keyword in statement.value.keywords:
                if keyword.arg == keyword_name:
                    return ast.unparse(keyword.value)
    raise AssertionError(f"{assignment_name}.{keyword_name} not found")


def test_mcp_service_clients_use_mcp_specific_timeouts() -> None:
    assert (
        _container_assignment_keyword("mcp_service_client", "timeout")
        == "settings.MCP_DEFAULT_TIMEOUT_SECONDS"
    )
    assert (
        _container_assignment_keyword("sandbox_mcp_service_client", "timeout")
        == "settings.SANDBOX_TIMEOUT_SECONDS"
    )
