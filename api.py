# Load-balanced CPU endpoint: OpenAI-compatible API facade.
#
# The point of this file: an app already written against the OpenAI API
# (here: playlist-visuals, a Spotify cover-art generator) migrates to
# self-hosted open models by changing ONE base URL. The LB endpoint runs the
# HTTP layer on cheap CPU workers and awaits the right-sized GPU workers —
# Flash handles remote dispatch, scaling, and auth (requests without a valid
# Runpod API key get a 401 before they reach this code).
#
# Supported (what playlist-visuals actually calls):
#   POST /v1/chat/completions   -> chat_worker  (Qwen2.5-7B-Instruct, 24 GB)
#   POST /v1/images/generations -> image_worker (FLUX.1-schnell, 48 GB)
#   GET  /v1/models, GET /health
# Not supported (honest limits, returned as clear errors): streaming,
# multimodal content, n > 1.
import time
import uuid

from runpod_flash import Endpoint

import chat_worker
import image_worker

CHAT_MODEL = chat_worker.CHAT_MODEL
IMAGE_MODEL = image_worker.IMAGE_MODEL

api = Endpoint(name="openai_api", cpu="cpu3c-1-2", workers=(1, 2))


def _error(message: str, status: int = 400) -> dict:
    # OpenAI-shaped error body; callers check `error` in the JSON.
    return {"error": {"message": message, "type": "invalid_request_error", "code": status}}


@api.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@api.get("/v1/models")
async def models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": CHAT_MODEL, "object": "model", "owned_by": "flash-cover-art"},
            {"id": IMAGE_MODEL, "object": "model", "owned_by": "flash-cover-art"},
        ],
    }


@api.post("/v1/chat/completions")
async def chat_completions(
    messages: list = None,
    model: str = "",
    max_tokens: int = 512,
    temperature: float = 0.7,
    stream: bool = False,
) -> dict:
    if not messages:
        return _error("'messages' is required")
    if stream:
        return _error("Streaming is not supported by this endpoint; set stream=false.")

    result = await chat_worker.chat_generate(
        messages=messages, max_tokens=max_tokens, temperature=temperature
    )
    if "error" in result:
        return _error(result["error"])

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": result["model"],
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result["content"]},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": result["usage"]["prompt_tokens"],
            "completion_tokens": result["usage"]["completion_tokens"],
            "total_tokens": result["usage"]["prompt_tokens"]
            + result["usage"]["completion_tokens"],
        },
    }


@api.post("/v1/images/generations")
async def images_generations(
    prompt: str = "",
    model: str = "",
    n: int = 1,
    size: str = "1024x1024",
    quality: str = "standard",
    response_format: str = "url",
) -> dict:
    if not prompt:
        return _error("'prompt' is required")
    if n != 1:
        return _error("Only n=1 is supported.")
    try:
        width, height = (int(v) for v in size.split("x"))
    except ValueError:
        return _error(f"Invalid size '{size}'; expected WIDTHxHEIGHT, e.g. 1024x1024")

    result = await image_worker.image_generate(prompt=prompt, width=width, height=height)
    if "error" in result:
        return _error(str(result["error"]))

    b64 = result["b64_jpeg"]
    datum: dict = {
        # FLUX does not rewrite prompts (unlike DALL-E 3); echo the original
        # so clients that display `revised_prompt` keep working.
        "revised_prompt": prompt,
    }
    if response_format == "b64_json":
        datum["b64_json"] = b64
    else:
        # No object storage required: a data URL is self-contained and never
        # expires (DALL-E's hosted URLs expire within hours).
        datum["url"] = f"data:image/jpeg;base64,{b64}"

    return {"created": int(time.time()), "data": [datum]}
