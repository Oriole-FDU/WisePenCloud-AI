import base64
import io

from matplotlib.figure import Figure

from chat.application.tools.chart.services.stat_chart.models import StatChartArtifact


class StatChartExporter:
    """将统计图 Figure 导出为 SVG/PNG artifact。"""

    def export(self, figure: Figure, output_formats: list[str]) -> list[StatChartArtifact]:
        """按请求格式导出图像。

        Args:
            figure: renderer 生成的 Matplotlib Figure。
            output_formats: tool 层收敛后的输出格式。

        Returns:
            SVG text 和/或 PNG base64 artifact。
        """
        artifacts: list[StatChartArtifact] = []
        for output_format in output_formats:
            if output_format == "svg":
                text_buffer = io.StringIO()
                figure.savefig(text_buffer, format="svg")
                artifacts.append(
                    StatChartArtifact(
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
                    StatChartArtifact(
                        kind="png",
                        mime_type="image/png",
                        content=base64.b64encode(bytes_buffer.getvalue()).decode("ascii"),
                        encoding="base64",
                    )
                )
        return artifacts
