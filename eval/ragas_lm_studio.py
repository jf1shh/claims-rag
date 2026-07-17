"""
Wires Ragas up to a local LM Studio server as the judge model, entirely offline.

Two real compatibility gaps had to be worked around here, both discovered by
actually running this against the installed package versions rather than
trusting documentation:

1. ragas==0.4.3 unconditionally imports `langchain_community.chat_models.vertexai
   .ChatVertexAI` purely to list it in an isinstance-check tuple it never
   otherwise uses. That module was removed from langchain-community when
   VertexAI support was split into a standalone partner package, so the
   import fails on any current install. We inject a throwaway stub module
   into sys.modules before importing ragas rather than editing site-packages
   (which wouldn't survive a fresh `pip install`).

2. `ragas.llms.llm_factory(..., provider="openai")` hardcodes
   `instructor.Mode.JSON` (plain `response_format: json_object`). LM Studio's
   OpenAI-compatible endpoint rejects that and requires `json_schema` mode.
   We bypass llm_factory and construct the instructor-wrapped client
   ourselves with `instructor.Mode.JSON_SCHEMA`.
"""
import sys
import types


def _install_vertexai_shim():
    if "langchain_community.chat_models.vertexai" in sys.modules:
        return
    shim = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:  # never instantiated; ragas only references the symbol
        pass

    shim.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = shim


_install_vertexai_shim()

import instructor
from openai import AsyncOpenAI
from ragas.llms.base import InstructorLLM, InstructorModelArgs


def get_lm_studio_judge(model: str, base_url: str = "http://127.0.0.1:1234/v1", max_tokens: int = 4096) -> InstructorLLM:
    """Returns a Ragas-compatible LLM wrapper pointed at a local LM Studio server.

    max_tokens defaults to 4096, well above InstructorModelArgs's default 1024 --
    the Faithfulness metric's statement-extraction step needs to enumerate every
    atomic claim plus a verdict for each, in JSON, and 1024 tokens truncates mid-output
    on claim-scoped queries with several retrieved chunks (observed empirically: 5/19
    queries silently returned no faithfulness score in the first eval run).
    """
    async_client = AsyncOpenAI(api_key="not-needed", base_url=base_url)
    instructor_client = instructor.from_openai(async_client, mode=instructor.Mode.JSON_SCHEMA)
    return InstructorLLM(
        client=instructor_client,
        model=model,
        provider="openai",
        model_args=InstructorModelArgs(max_tokens=max_tokens),
    )
