"""The three concrete model seams supported by the pinned llama.cpp runtime.

The server applies each GGUF's embedded template. These adapters pin that
template's identity, supply its arguments, and split its raw response. No
dataset, scorer, or report code needs to know model-specific delimiters.
"""

from __future__ import annotations

import re
from typing import Any


PHI_SOURCE_REVISIONS = (
    "1de18ec97600877ce63dbf60c73b998da99f0195",
    "69baf8528e1bcf05f475034d9e5dd32875ed125f",
)
GEMMA_SOURCE_REVISION = "43c9d014b231474fa7212dbb48a762566c2b255d"
PHI_TEMPLATE_SHA256 = "5a4c7c0b12bdbbc7110aebe80b15ac3d4dbabde85be466925dc2cf52d7698ceb"
GEMMA_TEMPLATE_SHA256 = "ae53464bf3be25802b3a5b37def7fd89667067d7577049b3b2d74c4d8de4c6d4"
QWEN_TEMPLATE_SHA256 = "57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361"
ADAPTER_VERSION = "1.0.0"


def _split_blocks(raw: str, opening: str, closing: str) -> tuple[str | None, str, str]:
    last_open = raw.rfind(opening)
    if last_open < 0:
        return None, raw, "absent"
    last_close = raw.rfind(closing)
    thinking = "".join(re.findall(re.escape(opening) + r"(.*?)(?:" +
                                  re.escape(closing) + r"|$)", raw, re.DOTALL))
    if last_open > last_close:
        return thinking, "", "unterminated"
    return thinking, raw[last_close + len(closing):], "present" if thinking.strip() else "empty"


class Qwen3Adapter:
    id = "qwen3"
    alias = "qwen3-8b"
    template_sha256 = QWEN_TEMPLATE_SHA256

    def settings(self, mode: str) -> tuple[str, dict[str, Any]]:
        flags = {"hybrid": "auto", "forced_think": "on", "forced_non_think": "off"}
        if mode not in flags:
            raise ValueError("invalid qwen3 reasoning mode")
        return flags[mode], {}

    def split(self, raw: str) -> tuple[str | None, str, str]:
        return _split_blocks(raw, "<think>", "</think>")


class Phi4ReasoningAdapter:
    id = "phi4-reasoning"
    template_sha256 = PHI_TEMPLATE_SHA256

    def __init__(self, model_name: str) -> None:
        if model_name not in ("phi-4-reasoning", "phi-4-reasoning-plus"):
            raise ValueError("unsupported Phi-4 model alias")
        self.alias = model_name
        self.source_revision = (PHI_SOURCE_REVISIONS[0] if model_name == "phi-4-reasoning"
                                else PHI_SOURCE_REVISIONS[1])

    def settings(self, mode: str) -> tuple[str, dict[str, Any]]:
        if mode != "always_on":
            raise ValueError("Phi-4 reasoning only permits always_on")
        return "auto", {}

    def split(self, raw: str) -> tuple[str | None, str, str]:
        return _split_blocks(raw, "<think>", "</think>")


class Gemma4Adapter:
    id = "gemma4"
    alias = "gemma-4-12b"
    template_sha256 = GEMMA_TEMPLATE_SHA256
    source_revision = GEMMA_SOURCE_REVISION

    def settings(self, mode: str) -> tuple[str, dict[str, Any]]:
        if mode not in ("thinking_on", "thinking_off"):
            raise ValueError("invalid Gemma 4 reasoning mode")
        # b10412 reads this boolean from keyed chat_template_kwargs per request.
        return "auto", {"enable_thinking": mode == "thinking_on"}

    def split(self, raw: str) -> tuple[str | None, str, str]:
        return _split_blocks(raw, "<|channel>thought\n", "<channel|>")


def from_protocol(protocol: dict[str, Any]) -> Qwen3Adapter | Phi4ReasoningAdapter | Gemma4Adapter:
    """Select only the named adapters; an omitted identity means legacy Qwen3."""
    model = protocol.get("model", {})
    identity = model.get("adapter")
    role = protocol.get("role", "target")
    alias = protocol.get("runtime", {}).get("model_alias")
    if identity is None:
        selected = Qwen3Adapter()
    elif not isinstance(identity, dict) or identity.get("version") != ADAPTER_VERSION:
        raise ValueError("unsupported model adapter version")
    elif identity.get("id") == "qwen3":
        selected = Qwen3Adapter()
    elif identity.get("id") == "phi4-reasoning":
        selected = Phi4ReasoningAdapter(alias if role != "fixture" else "phi-4-reasoning")
    elif identity.get("id") == "gemma4":
        selected = Gemma4Adapter()
    else:
        raise ValueError("unsupported model adapter")
    if role == "fixture" and identity is not None:
        if not isinstance(alias, str) or re.fullmatch(r"fixture-[0-9]+", alias) is None:
            raise ValueError("fixture model alias must be fixture-<n>")
        selected.alias = alias
        if isinstance(selected, Phi4ReasoningAdapter):
            selected.source_revision = None
    elif alias is not None and alias != selected.alias:
        raise ValueError("runtime model alias differs from adapter")
    mode = protocol.get("reasoning_mode", {}).get("value")
    if mode is None and identity is None:
        mode = "hybrid"  # Allow old protocol readers to reach artifact checks.
    selected.settings(mode)
    return selected
