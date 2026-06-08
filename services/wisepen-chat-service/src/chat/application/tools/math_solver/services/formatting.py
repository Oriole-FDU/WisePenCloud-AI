from typing import Any

from chat.application.tools.math_solver.services.models import MathSolverResult


def stringify(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        items = [f"{stringify(key)}: {stringify(item)}" for key, item in value.items()]
        return "{" + ", ".join(items) + "}"
    return str(value)


def format_math_solver_result(tool_name: str, result: MathSolverResult) -> str:
    lines = [
        f"[Tool Result] {tool_name}",
        "",
        f"Backend: {result.backend}",
        f"Task: {result.task}",
    ]

    if result.exact_result is not None:
        lines.append(f"Exact result: {stringify(result.exact_result)}")
    if result.numeric_result is not None:
        lines.append(f"Numeric result: {result.numeric_result}")
    if result.latex_result is not None:
        lines.append(f"LaTeX result: {result.latex_result}")
    if result.notes:
        lines.append("")
        lines.append("Notes:")
        for note in result.notes:
            lines.append(f"- {note}")

    if result.backend == "sage":
        first_instruction = "Use this SageMath result as the authoritative advanced computation result."
    else:
        first_instruction = "Use this result as a deterministic computation result."

    lines.extend(
        [
            "",
            "Assistant instructions:",
            f"- {first_instruction}",
            "- Preserve exact form before numerical approximation.",
            "- This is not a formal proof certificate.",
        ]
    )
    return "\n".join(lines)


def format_math_solver_error(
    tool_name: str,
    task: str,
    reason: str,
    retryable: bool,
) -> str:
    retryable_text = "true" if retryable else "false"
    return "\n".join(
        [
            f"[Tool Error] {tool_name} failed",
            f"Task: {task}",
            f"Reason: {reason}",
            f"Retryable: {retryable_text}",
            "",
            "Assistant instructions:",
            "- Treat this as a tool-call argument or computation failure, not as the final answer.",
            "- If Retryable is true, correct the tool arguments according to Reason and retry.",
        ]
    )
