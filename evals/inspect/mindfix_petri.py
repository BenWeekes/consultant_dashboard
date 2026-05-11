from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import dotenv_values
from inspect_ai.model import GenerateConfig, get_model

from evals.prompting import assemble_effective_prompt


INSPECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = INSPECT_DIR.parents[2]
SIMPLE_BACKEND_ENV = REPO_ROOT / "agent-samples" / "simple-backend" / ".env"
SERVER_CUSTOM_LLM_ENV = REPO_ROOT / "server-custom-llm" / "node" / ".env"


def load_base_prompt() -> str:
    values = dotenv_values(str(SIMPLE_BACKEND_ENV))
    prompt = str(values.get("THERAPY_DEFAULT_PROMPT") or "").strip()
    if not prompt:
        raise RuntimeError(f"THERAPY_DEFAULT_PROMPT missing in {SIMPLE_BACKEND_ENV}")
    return prompt.replace("\\n", "\n")


def load_context(context_path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(context_path).read_text())


def effective_target_prompt(context_path: str | Path) -> str:
    return assemble_effective_prompt(load_base_prompt(), load_context(context_path))


def _default_target_api_key() -> str:
    values = dotenv_values(str(SERVER_CUSTOM_LLM_ENV))
    api_key = str(values.get("LLM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"LLM_API_KEY missing in {SERVER_CUSTOM_LLM_ENV}")
    return api_key


def _base_generate_config(*, system_message: str | None = None) -> GenerateConfig:
    effort = (os.environ.get("MINDFIX_INSPECT_REASONING_EFFORT") or "medium").strip()
    return GenerateConfig(
        system_message=system_message,
        reasoning_effort=effort,
        timeout=60,
        max_retries=1,
        extra_body={"stream": False},
    )


def _model_name(env_name: str, default: str) -> str:
    return (os.environ.get(env_name) or default).strip()


def _target_base_url() -> str:
    return (os.environ.get("MINDFIX_INSPECT_TARGET_BASE_URL") or "http://127.0.0.1:8101").strip()


def _target_api_key() -> str:
    return (os.environ.get("MINDFIX_INSPECT_TARGET_API_KEY") or _default_target_api_key()).strip()


def target_connection_settings() -> Dict[str, str]:
    provider_model = _model_name("MINDFIX_INSPECT_TARGET_MODEL", "openai-api/mindfix/gpt-5.4-mini")
    return {
        "base_url": _target_base_url(),
        "api_key": _target_api_key(),
        "provider_model": provider_model,
        "model": provider_model.rsplit("/", 1)[-1],
        "reasoning_effort": (os.environ.get("MINDFIX_INSPECT_REASONING_EFFORT") or "medium").strip(),
    }


def build_target_model(*, context_path: str | Path):
    settings = target_connection_settings()
    return get_model(
        settings["provider_model"],
        api_key=settings["api_key"],
        base_url=settings["base_url"],
        config=_base_generate_config(system_message=effective_target_prompt(context_path)),
        responses_api=False,
    )


def build_auditor_model():
    return get_model(
        _model_name("MINDFIX_INSPECT_AUDITOR_MODEL", "openai-api/mindfix/gpt-5.4-mini"),
        api_key=_target_api_key(),
        base_url=_target_base_url(),
        config=_base_generate_config(),
        responses_api=False,
    )


def build_judge_model():
    return get_model(
        _model_name("MINDFIX_INSPECT_JUDGE_MODEL", "openai-api/mindfix/gpt-5.4-mini"),
        api_key=_target_api_key(),
        base_url=_target_base_url(),
        config=_base_generate_config(),
        responses_api=False,
    )
