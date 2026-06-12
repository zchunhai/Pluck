"""Built-in model provider configurations."""

from pluck.providers import ProviderConfig, ModelTier

# Convert to ProviderConfig format
BUILTIN_PROVIDERS: dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        name="anthropic",
        display_name="Anthropic (Official)",
        base_url="https://api.anthropic.com",
        models={
            "opus": ModelTier(id="claude-opus-4-8"),
            "sonnet": ModelTier(id="claude-sonnet-4-6"),
            "haiku": ModelTier(id="claude-haiku-4-5"),
        },
        default_tier="sonnet",
    ),
    "zhipu": ProviderConfig(
        name="zhipu",
        display_name="智谱 AI",
        base_url="https://open.bigmodel.cn/api/anthropic",
        models={
            "opus": ModelTier(id="glm-5.1"),
            "sonnet": ModelTier(id="glm-5-turbo"),
            "haiku": ModelTier(id="glm-4.7"),
        },
        default_tier="sonnet",
    ),
    "deepseek": ProviderConfig(
        name="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        models={
            "sonnet": ModelTier(id="deepseek-chat"),
            "haiku": ModelTier(id="deepseek-chat"),
        },
        default_tier="sonnet",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        models={
            "opus": ModelTier(id="anthropic/claude-opus-4"),
            "sonnet": ModelTier(id="anthropic/claude-sonnet-4"),
            "haiku": ModelTier(id="anthropic/claude-haiku-4"),
        },
        default_tier="sonnet",
    ),
}
