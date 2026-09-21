# Queue-based GPU worker: text generation with Qwen2.5-7B-Instruct.
#
# Serves the playlist-summary half of the OpenAI-compatible API. Right-sized
# to a 24 GB card (model is ~15 GB in bf16) — pinned per Flash best practices
# rather than GpuType.ANY, which handed us an H100 for hello-world.
from runpod_flash import Endpoint, GpuType

from resources import DATACENTER, HF_CACHE_VOLUME, HF_ENV

CHAT_MODEL = "Qwen/Qwen2.5-7B-Instruct"

# Lazy-loaded once per worker process; warm requests reuse it.
_state: dict = {}


@Endpoint(
    name="chat_worker",
    gpu=GpuType.NVIDIA_GEFORCE_RTX_4090,
    min_cuda_version="12.4",
    workers=(0, 1),
    idle_timeout=300,
    # Pin below v5: flash build resolves deps at build time and bundles them
    # into the artifact — ">=4.44" silently pulled transformers 5.x, whose
    # generation API breaks v4-era code at runtime.
    dependencies=["transformers>=4.44,<5", "accelerate"],
    volume=HF_CACHE_VOLUME,
    datacenter=DATACENTER,
    env=HF_ENV,
    execution_timeout_ms=600_000,
)
async def chat_generate(
    messages: list,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> dict:
    """Generate a chat completion. Returns OpenAI-shaped message + usage."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    for m in messages:
        if not isinstance(m.get("content"), str):
            return {
                "error": "Only plain-text message content is supported. "
                "Multimodal content (e.g. image_url) is not available on this endpoint."
            }

    if "model" not in _state:
        _state["tokenizer"] = AutoTokenizer.from_pretrained(CHAT_MODEL)
        _state["model"] = AutoModelForCausalLM.from_pretrained(
            CHAT_MODEL, torch_dtype=torch.bfloat16, device_map="cuda"
        )
    tokenizer, model = _state["tokenizer"], _state["model"]

    # Two-step: render template to text, then tokenize. Stable across
    # transformers major versions, unlike tokenized template output.
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    encoded = tokenizer(text, return_tensors="pt").to(model.device)
    input_ids = encoded["input_ids"]

    output_ids = model.generate(
        **encoded,
        max_new_tokens=max_tokens,
        do_sample=temperature > 0,
        temperature=max(temperature, 1e-3),
        pad_token_id=tokenizer.eos_token_id,
    )
    completion_ids = output_ids[0][input_ids.shape[1]:]
    text = tokenizer.decode(completion_ids, skip_special_tokens=True)

    return {
        "content": text,
        "model": CHAT_MODEL,
        "usage": {
            "prompt_tokens": int(input_ids.shape[1]),
            "completion_tokens": int(completion_ids.shape[0]),
        },
    }
