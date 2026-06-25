"""
azure_client.py (v2)

Thin wrapper around Azure OpenAI Structured Outputs. Responsibilities:

  * to_strict_schema(model): turn a Pydantic model into the strict JSON Schema
    Azure OpenAI requires (additionalProperties:false everywhere, every property
    required). In strict mode the model cannot return a non-conforming shape.
  * extract(model, system, user): call the EXTRACTION deployment and parse the
    response straight into the Pydantic model.
  * judge(model, system, user): same, but against the REASONING deployment used
    for harder judgment calls (e.g. clause-pair diffs).

Auth prefers Entra ID (managed identity / app token via azure-identity) so no
key sits in config; an API key is accepted as a local-dev fallback. Contract
text stays within your Azure region and is not used to train models.

    pip install openai azure-identity
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel

from config import settings

T = TypeVar("T", bound=BaseModel)

# Cognitive Services scope for Entra ID auth against Azure OpenAI.
_AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"

_client = None


# --------------------------------------------------------------------------- #
# Strict JSON Schema
# --------------------------------------------------------------------------- #
def to_strict_schema(model: Type[BaseModel]) -> Dict[str, Any]:
    """Pydantic model -> strict JSON Schema accepted by Structured Outputs."""
    schema = copy.deepcopy(model.model_json_schema())
    _strictify(schema)
    return schema


def _strictify(node: Any) -> None:
    if isinstance(node, dict):
        # strict mode rejects defaults; identity of fields comes from required.
        node.pop("default", None)
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties", {})
            node["additionalProperties"] = False
            node["required"] = list(props.keys())
        for value in node.values():
            _strictify(value)
    elif isinstance(node, list):
        for item in node:
            _strictify(item)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from openai import AzureOpenAI
    except ImportError as exc:
        raise RuntimeError("pip install openai for the Azure OpenAI client.") from exc
    if not settings.azure_openai_endpoint:
        raise RuntimeError("Set AZURE_OPENAI_ENDPOINT.")

    kwargs: Dict[str, Any] = {
        "azure_endpoint": settings.azure_openai_endpoint,
        "api_version": settings.azure_openai_api_version,
    }
    if settings.azure_openai_api_key:
        kwargs["api_key"] = settings.azure_openai_api_key  # local-dev fallback
    else:
        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ImportError as exc:
            raise RuntimeError(
                "pip install azure-identity, or set AZURE_OPENAI_API_KEY for local dev."
            ) from exc
        kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
            DefaultAzureCredential(), _AOAI_SCOPE
        )
    _client = AzureOpenAI(**kwargs)
    return _client


# --------------------------------------------------------------------------- #
# Calls
# --------------------------------------------------------------------------- #
def _call(model: Type[T], system: str, user: str, deployment: str,
          temperature: Optional[float] = 0.0) -> T:
    client = _get_client()
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": model.__name__, "schema": to_strict_schema(model), "strict": True},
    }
    kwargs: Dict[str, Any] = {
        "model": deployment,
        "response_format": response_format,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    # Reasoning deployments (o-series) reject the temperature parameter — only the
    # default is allowed — so pass temperature=None to omit it entirely.
    if temperature is not None:
        kwargs["temperature"] = temperature
    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0].message
    if getattr(choice, "refusal", None):
        raise RuntimeError(f"Model refused: {choice.refusal}")
    return model.model_validate(json.loads(choice.content))


def extract(model: Type[T], system: str, user: str) -> T:
    """Structured extraction on the fast deployment."""
    return _call(model, system, user, settings.azure_openai_deployment_extract, temperature=0.0)


def judge(model: Type[T], system: str, user: str) -> T:
    """Harder judgment (e.g. clause-pair diff) on the reasoning deployment.

    Temperature is omitted: o-series reasoning models only support the default.
    """
    return _call(model, system, user, settings.azure_openai_deployment_judge, temperature=None)


def which_models() -> Dict[str, Optional[str]]:
    """For AIExtractionRun.ModelVersion bookkeeping."""
    return {
        "extract": settings.azure_openai_deployment_extract,
        "judge": settings.azure_openai_deployment_judge,
    }


def map_terms(model, system, user):
    """Alias of extract() for the grounded subject-matter mapping step."""
    return extract(model, system, user)
