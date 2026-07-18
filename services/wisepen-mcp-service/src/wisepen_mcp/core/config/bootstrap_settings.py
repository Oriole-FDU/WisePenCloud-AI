from common.core.config.bootstrap_settings import BootstrapSettings


class McpBootstrapSettings(BootstrapSettings):
    APP_NAME: str = "WisePen MCP Service"
    SERVICE_NAME: str = "wisepen-mcp-service"
    SERVICE_PORT: int = 19911


bootstrap_settings = McpBootstrapSettings()
