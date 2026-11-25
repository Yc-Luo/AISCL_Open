import json
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from langchain_openai import ChatOpenAI
from langchain_community.llms import Ollama
from app.core.config import settings
from app.core.llm_runtime import attach_llm_metadata, select_api_key
from app.repositories.system_config import SystemConfig


def _is_real_secret(value: Optional[str]) -> bool:
    """Avoid treating masked UI placeholders as usable API keys."""
    return bool(value and "•••" not in value and value.strip())


def _normalize_provider(value: Optional[str]) -> str:
    """Normalize provider names entered in the admin panel."""
    provider = (value or "").strip().lower().replace(" ", "_")
    aliases = {
        "siliconflow": "openai_compatible",
        "openrouter": "openai_compatible",
        "dashscope": "openai_compatible",
        "aliyun": "openai_compatible",
        "qwen": "openai_compatible",
        "zhipu": "openai_compatible",
        "moonshot": "openai_compatible",
        "minimax": "openai_compatible",
        "openai-compatible": "openai_compatible",
    }
    return aliases.get(provider, provider)


async def _get_config_value(key: str) -> Optional[str]:
    config = await SystemConfig.find_one(SystemConfig.key == key)
    if not config:
        return None
    value = config.value.strip() if isinstance(config.value, str) else config.value
    return value or None


DEFAULT_MODEL_ALIASES = {
    "",
    "default",
    "default_chat_model",
    "follow_system_default",
    "system",
    "system_default",
}


def _is_default_model_id(model_id: Optional[str]) -> bool:
    return (model_id or "").strip() in DEFAULT_MODEL_ALIASES


async def _get_json_config(key: str, fallback: Any) -> Any:
    value = await _get_config_value(key)
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


async def _find_model_definition(model_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve model definitions from admin custom models or research model pool."""
    if _is_default_model_id(model_id):
        return None
    target = str(model_id).strip()
    for key in ("user_custom_models", "research_model_pool"):
        items = await _get_json_config(key, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id") or "").strip() == target:
                return item
    return None


class DedicatedModelUnavailable(ValueError):
    """An explicit model connection is missing or cannot use its own credentials."""


class _CompatibleChatOpenAI(ChatOpenAI):
    """Keep the token field used by compatible Chat Completions providers."""

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload


async def list_dedicated_chat_models() -> dict[str, dict]:
    """Return eligible administrator-defined connections for explicit class bindings."""
    from app.core.teacher_model_transport import normalize_teacher_endpoint
    from app.services.teacher_model_service import TEACHER_MODEL_PREFIX

    items = await _get_json_config("user_custom_models", [])
    models: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        model_id = str(item.get("id") or "").strip()
        provider = _normalize_provider(item.get("provider") or "openai_compatible")
        key = item.get("key") or item.get("api_key")
        if (
            _is_default_model_id(model_id) or model_id.startswith(TEACHER_MODEL_PREFIX)
            or provider not in {"openai", "openai_compatible", "deepseek", "deepseek-chat"}
            or not isinstance(key, str) or not _is_real_secret(key)
        ):
            continue
        try:
            base_url = normalize_teacher_endpoint(item.get("base_url") or item.get("url") or item.get("api_base") or "")
        except ValueError:
            continue
        models.setdefault(model_id, {
            "id": model_id, "name": str(item.get("name") or model_id).strip() or model_id,
            "provider": provider, "base_url": base_url, "api_key": key.strip(),
        })
    return models


@asynccontextmanager
async def course_dialogue_llm(
    *, model_id: str, owner_id: str, temperature: float,
    max_tokens: int | None = None, timeout: float | None = None,
):
    """Use the requested connection only; never borrow a system endpoint or key."""
    from app.core.teacher_model_transport import create_teacher_async_http_client, normalize_teacher_endpoint
    from app.services.teacher_model_service import TEACHER_MODEL_PREFIX, load_teacher_model_definition

    if model_id.startswith(TEACHER_MODEL_PREFIX):
        definition = await load_teacher_model_definition(owner_id, model_id)
    else:
        definition = (await list_dedicated_chat_models()).get(model_id)
    if not definition or not _is_real_secret(definition.get("api_key")):
        raise DedicatedModelUnavailable("Configured model connection is unavailable")
    try:
        base_url = normalize_teacher_endpoint(definition["base_url"])
    except ValueError:
        raise DedicatedModelUnavailable("Configured model endpoint is unavailable") from None
    api_key = definition["api_key"]
    model_name = definition["id"]
    timeout = timeout or settings.LLM_REQUEST_TIMEOUT_SECONDS
    official_openai = definition["provider"] == "openai" and base_url == "https://api.openai.com/v1"
    chat_model = ChatOpenAI if official_openai else _CompatibleChatOpenAI
    async with create_teacher_async_http_client(base_url, timeout=timeout) as client:
        model = chat_model(
            model=model_name, temperature=None if official_openai else temperature, openai_api_key=api_key,
            openai_api_base=base_url, http_async_client=client, timeout=timeout,
            max_tokens=max_tokens or settings.LLM_MAX_OUTPUT_TOKENS, max_retries=0,
            use_responses_api=False,
        )
        yield attach_llm_metadata(model, provider=definition["provider"], api_key=api_key, model=model_name)


async def _get_role_model_map() -> Dict[str, str]:
    value = None
    if settings.LLM_CONFIG_SOURCE.lower() == "db":
        value = await _get_config_value("llm_role_model_map")
    value = value or settings.LLM_ROLE_MODEL_MAP
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    role_map: Dict[str, str] = {}
    for key, item in parsed.items():
        if isinstance(item, str):
            role_map[str(key).strip()] = item.strip()
        elif isinstance(item, dict) and item.get("model_id"):
            role_map[str(key).strip()] = str(item["model_id"]).strip()
    return {key: value for key, value in role_map.items() if key and value}


async def resolve_role_model_id(role_name: str, fallback_model_id: Optional[str] = None) -> Optional[str]:
    """Resolve optional per-role model routing without changing graph wiring."""
    role_map = await _get_role_model_map()
    normalized = (role_name or "").strip()
    aliases = {
        "问题推进者": "problem_progressor",
        "资料研究员": "evidence_researcher",
        "观点挑战者": "viewpoint_challenger",
        "反馈追问者": "feedback_prompter",
        "LangGraph监督者": "langgraph_supervisor",
        "LangGraph 监督者": "langgraph_supervisor",
        "监督者": "langgraph_supervisor",
        "路由决策者": "routing_decision",
        "编排规划器": "orchestration_planner",
        "检索规划器": "retrieval_planner",
        "回答综合器": "answer_synthesizer",
        "自动提示策略": "auto_prompt_policy",
        "小组记忆摘要器": "group_memory_summarizer",
    }
    candidates = [normalized, aliases.get(normalized, "")]
    for candidate in candidates:
        if candidate and role_map.get(candidate):
            return role_map[candidate]
    return fallback_model_id


async def get_llm(temperature: float = 0.7, model_id: Optional[str] = None):
    """Get LLM instance based on database or env configuration."""
    use_db_config = settings.LLM_CONFIG_SOURCE.lower() == "db"

    # 1. Try to get active model and custom definitions from DB
    active_model_id = None
    db_provider = None
    db_api_key = None
    db_base_url = None
    runtime_model_definition = None
    if use_db_config:
        try:
            active_model_id = await SystemConfig.find_one(SystemConfig.key == "llm_model")
            db_provider = await _get_config_value("llm_provider")
            db_api_key = await _get_config_value("llm_key")
            db_base_url = await _get_config_value("llm_base_url")
            db_key_pool = await _get_config_value("llm_key_pool")
            runtime_model_definition = await _find_model_definition(model_id)
        except Exception as e:
            print(f"[LLMConfig] Error fetching custom LLM config: {e}")
            db_key_pool = None
    else:
        db_key_pool = None

    # 2. Fallback to default providers based on settings/active_model_id
    provider = _normalize_provider(db_provider or settings.LLM_PROVIDER)
    model_name = None
    override_base_url = None
    override_api_key = None
    
    if runtime_model_definition:
        model_name = str(runtime_model_definition.get("id") or model_id or "").strip()
        provider = _normalize_provider(runtime_model_definition.get("provider") or provider)
        override_base_url = (
            runtime_model_definition.get("base_url")
            or runtime_model_definition.get("url")
            or runtime_model_definition.get("api_base")
        )
        override_api_key = (
            runtime_model_definition.get("key")
            or runtime_model_definition.get("api_key")
        )
        print(f"[LLMConfig] Using runtime model: {runtime_model_definition.get('name') or model_name} ({model_name})")
    elif not _is_default_model_id(model_id):
        model_name = str(model_id).strip()
    elif use_db_config and active_model_id:
        model_name = active_model_id.value
        if not db_provider and model_name in ["gpt-4o", "gpt-3.5-turbo"]:
            provider = "openai"
        elif not db_provider and model_name in ["deepseek-chat", "deepseek-reasoner"]:
            provider = "deepseek"
        elif not db_provider and model_name == "ollama":
            provider = "ollama"
    
    print(f"[LLMConfig] Initializing provider: {provider}, model: {model_name or 'default'}")

    if provider in ["openai", "openai_compatible"]:
        api_key = (
            override_api_key
            if _is_real_secret(override_api_key)
            else db_api_key if use_db_config and _is_real_secret(db_api_key)
            else settings.OPENAI_API_KEY
        )
        if use_db_config:
            db_key = await SystemConfig.find_one(SystemConfig.key == "llm_key")
            api_key = (
                api_key
                if _is_real_secret(override_api_key)
                else db_key.value if db_key and _is_real_secret(db_key.value)
                else api_key
            )
        if not _is_real_secret(override_api_key):
            api_key = select_api_key("openai", api_key, db_key_pool or settings.OPENAI_API_KEYS)

        llm_kwargs = {
            "model": model_name or settings.OPENAI_MODEL,
            "temperature": temperature,
            "openai_api_key": api_key,
            "max_tokens": settings.LLM_MAX_OUTPUT_TOKENS,
            "timeout": settings.LLM_REQUEST_TIMEOUT_SECONDS,
            "max_retries": 2,
        }
        base_url = override_base_url or (db_base_url if use_db_config and db_base_url else settings.OPENAI_BASE_URL)
        if base_url:
            llm_kwargs["openai_api_base"] = base_url
        return attach_llm_metadata(
            ChatOpenAI(**llm_kwargs),
            provider=provider,
            api_key=api_key,
            model=llm_kwargs["model"],
        )
    elif provider == "ollama":
        model = model_name or settings.OLLAMA_MODEL
        return attach_llm_metadata(Ollama(
            model=model_name or settings.OLLAMA_MODEL,
            base_url=override_base_url or (db_base_url if use_db_config and db_base_url else settings.OLLAMA_BASE_URL),
            temperature=temperature,
        ), provider=provider, api_key=None, model=model)
    elif provider in ["deepseek", "deepseek-chat"]:
        api_key = (
            override_api_key
            if _is_real_secret(override_api_key)
            else db_api_key if use_db_config and _is_real_secret(db_api_key)
            else settings.DEEPSEEK_API_KEY
        )
        if use_db_config:
            db_key = await SystemConfig.find_one(SystemConfig.key == "llm_key")
            api_key = (
                api_key
                if _is_real_secret(override_api_key)
                else db_key.value if db_key and _is_real_secret(db_key.value)
                else api_key
            )
        if not _is_real_secret(override_api_key):
            api_key = select_api_key("deepseek", api_key, db_key_pool or settings.DEEPSEEK_API_KEYS)
        model = model_name or settings.DEEPSEEK_MODEL
        return attach_llm_metadata(ChatOpenAI(
            model=model_name or settings.DEEPSEEK_MODEL,
            temperature=temperature,
            openai_api_key=api_key,
            openai_api_base=override_base_url or (db_base_url if use_db_config and db_base_url else settings.DEEPSEEK_BASE_URL),
            max_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=2,
        ), provider=provider, api_key=api_key, model=model)
    else:
        raise ValueError(f"Unsupported AI provider: {provider}")


async def get_llm_for_role(role_name: str, temperature: Optional[float] = None):
    """Get LLM instance for a specific AI role (Async)."""
    if temperature is None:
        temperature = 0.7
    model_id = await resolve_role_model_id(role_name)
    return await get_llm(temperature=temperature, model_id=model_id)
