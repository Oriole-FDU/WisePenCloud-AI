from __future__ import annotations

from decimal import Decimal

from common.core.config.app_settings import (
    ConfigModel,
    InternalSecuritySettings,
    RpcSettings,
    ServiceDiscoverySettings,
)
from pydantic import Field, model_validator


class RedisSettings(ConfigModel):
    url: str = Field(min_length=1)


class MongoDbSettings(ConfigModel):
    url: str = Field(min_length=1)
    database: str = Field(min_length=1)


class QdrantSettings(ConfigModel):
    host: str = Field(min_length=1)
    port: int = Field(default=6333, ge=1, le=65535)
    api_key: str = Field(min_length=1)


class PersistenceSettings(ConfigModel):
    redis: RedisSettings
    mongodb: MongoDbSettings
    qdrant: QdrantSettings


class KafkaSettings(ConfigModel):
    bootstrap_servers: str = Field(min_length=1)


class MessagingSettings(ConfigModel):
    kafka: KafkaSettings


class ProviderConnectionSettings(ConfigModel):
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)


class MemoryModelSettings(ConfigModel):
    llm_model: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_dims: int = Field(default=1024, ge=1)


class SummaryModelSettings(ConfigModel):
    model: str = Field(min_length=1)


class RerankerModelSettings(ConfigModel):
    model: str = Field(min_length=1)


class ModelSettings(ConfigModel):
    memory: MemoryModelSettings
    summary: SummaryModelSettings
    reranker: RerankerModelSettings


class ZeroEntropySettings(ConfigModel):
    api_key: str = Field(min_length=1)


class IflytekSpeechSettings(ConfigModel):
    app_id: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)


class SpeechSettings(ConfigModel):
    iflytek: IflytekSpeechSettings | None = None


class IntegrationSettings(ConfigModel):
    common_llm_provider: ProviderConnectionSettings
    common_embedding_provider: ProviderConnectionSettings
    zero_entropy: ZeroEntropySettings
    speech: SpeechSettings | None = None


class BillingSettings(ConfigModel):
    student_service_fee_rate: Decimal = Field(default=Decimal("0.10"), ge=0)
    teacher_service_fee_rate: Decimal = Field(default=Decimal("0.50"), ge=0)


class ContextCompressionSettings(ConfigModel):
    high_watermark_ratio: float = Field(default=0.8, gt=0, le=1)
    low_watermark_ratio: float = Field(default=0.5, ge=0, lt=1)

    @model_validator(mode="after")
    def validate_watermarks(self) -> ContextCompressionSettings:
        if self.low_watermark_ratio >= self.high_watermark_ratio:
            raise ValueError(
                "low-watermark-ratio must be lower than high-watermark-ratio"
            )
        return self


class LongTermMemorySettings(ConfigModel):
    limit: int = Field(default=10, ge=1)
    threshold: float = Field(default=0.6, ge=0, le=1)


class ContextSettings(ConfigModel):
    token_limit: int = Field(default=128000, ge=1)
    default_output_reserve_tokens: int = Field(default=4096, ge=1)
    min_prompt_budget_tokens: int = Field(default=1024, ge=1)
    compression: ContextCompressionSettings = Field(
        default_factory=ContextCompressionSettings
    )
    fallback_history_limit: int = Field(default=20, ge=1)
    long_term_memory: LongTermMemorySettings = Field(
        default_factory=LongTermMemorySettings
    )


class VisionSettings(ConfigModel):
    max_image_count: int = Field(default=10, ge=1)
    max_image_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    max_total_image_bytes: int = Field(default=12 * 1024 * 1024, ge=1)
    enable_history_image_auto_load: bool = True
    history_image_turn_limit: int = Field(default=5, ge=0)


class AgentSettings(ConfigModel):
    max_iterations: int = Field(default=25, ge=1)


class ChatTurnSettings(ConfigModel):
    active_ttl_seconds: int = Field(default=30 * 60, ge=1)
    stream_ttl_seconds: int = Field(default=60 * 60, ge=1)


class ToolContentSettings(ConfigModel):
    preview_per_char_budget: int = Field(default=4_000, ge=1)
    preview_total_char_budget: int = Field(default=12_000, ge=1)
    read_window_char_budget: int = Field(default=24_000, ge=1)
    default_ttl_seconds: int = Field(default=3600, ge=1)


class ToolSettings(ConfigModel):
    result_max_chars: int = Field(default=4000, ge=1)
    content: ToolContentSettings = Field(default_factory=ToolContentSettings)


class SkillSettings(ConfigModel):
    match_top_k: int = Field(default=20, ge=1)


class McpSettings(ConfigModel):
    system_list_tools_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    user_list_tools_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    default_timeout_seconds: float = Field(default=15.0, gt=0)
    max_user_servers: int = Field(default=10, ge=1)
    max_tools_per_server: int = Field(default=50, ge=1)


class AssetCacheSettings(ConfigModel):
    directory: str = Field(default="/var/oss_cache", min_length=1)
    ttl_seconds: int = Field(default=6 * 3600, ge=1)
    gc_interval_seconds: int = Field(default=30 * 60, ge=1)


class ChatSettings(ConfigModel):
    security: InternalSecuritySettings
    persistence: PersistenceSettings
    messaging: MessagingSettings
    models: ModelSettings
    integrations: IntegrationSettings
    billing: BillingSettings = Field(default_factory=BillingSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    chat_turn: ChatTurnSettings = Field(default_factory=ChatTurnSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    skills: SkillSettings = Field(default_factory=SkillSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    rpc: RpcSettings = Field(default_factory=RpcSettings)
    service_discovery: ServiceDiscoverySettings = Field(
        default_factory=ServiceDiscoverySettings
    )
    asset_cache: AssetCacheSettings = Field(default_factory=AssetCacheSettings)
