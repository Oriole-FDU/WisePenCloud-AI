import base64
import io

from matplotlib.figure import Figure

from chat.application.tools.chart.services.function_plot.models import (
    FunctionPlotArtifact,
)


class FunctionPlotExporter:
    """将 Matplotlib Figure 导出为 tool artifact。"""

    def export(self, figure: Figure, output_formats: list[str]) -> list[FunctionPlotArtifact]:
        """按请求格式导出 SVG/PNG。

        Args:
            figure: renderer 生成的 Matplotlib Figure。
            output_formats: tool 层确认过的输出格式列表。

        Returns:
            artifact 列表。SVG 使用 text，PNG 使用 base64。
        """
        artifacts: list[FunctionPlotArtifact] = []
        for output_format in output_formats:
            if output_format == "svg":
                text_buffer = io.StringIO()
                figure.savefig(text_buffer, format="svg")
                artifacts.append(
                    FunctionPlotArtifact(
                        kind="svg",
                        mime_type="image/svg+xml",
                        content=text_buffer.getvalue(),
                        encoding="text",
                    )
                )
            elif output_format == "png":
                bytes_buffer = io.BytesIO()
                figure.savefig(bytes_buffer, format="png")
                artifacts.append(
                    FunctionPlotArtifact(
                        kind="png",
                        mime_type="image/png",
                        content=base64.b64encode(bytes_buffer.getvalue()).decode("ascii"),
                        encoding="base64",
                    )
                )
        return artifacts
