# Queue-based GPU worker: album-cover image generation with FLUX.1-schnell.
#
# FLUX.1-schnell (Apache-2.0, no HF token needed) runs 4-step generation —
# well suited to interactive cover art. ~34 GB in bf16 incl. text encoders,
# so this pins a 48 GB card.
from runpod_flash import Endpoint, GpuType

from resources import DATACENTER, HF_CACHE_VOLUME, HF_ENV

IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"

_state: dict = {}


@Endpoint(
    name="image_worker",
    # A single pinned A6000 had zero capacity in US_IL_1 (job sat IN_QUEUE,
    # no workers initializing). The 48GB Ampere *group* trades exact-card
    # predictability for availability while keeping the VRAM class.
    # Consumer cards with real stock in EU_RO_1 (checked via API, not
    # guessed). FLUX doesn't fit 24/32GB fully resident, so the pipeline
    # uses enable_model_cpu_offload() — components move to GPU only while
    # active. Slower per image than a 48GB card, but schedulable > fast.
    gpu=[GpuType.NVIDIA_GEFORCE_RTX_5090, GpuType.NVIDIA_GEFORCE_RTX_4090],
    min_cuda_version="12.4",
    workers=(0, 1),
    idle_timeout=300,
    dependencies=[
        # Upper-bounded: build-time resolution bundles whatever pip picks,
        # so unpinned ranges can jump major versions between deploys.
        "diffusers>=0.30,<1",
        "transformers>=4.44,<5",
        "accelerate",
        "sentencepiece",
        "protobuf",
    ],
    volume=HF_CACHE_VOLUME,
    datacenter=DATACENTER,
    env=HF_ENV,
    execution_timeout_ms=600_000,
)
async def image_generate(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
) -> dict:
    """Generate one image; returns base64 JPEG."""
    import base64
    import io

    import torch
    from diffusers import FluxPipeline

    if "pipe" not in _state:
        pipe = FluxPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=torch.bfloat16)
        # Keeps peak VRAM under 24GB (FLUX fully resident needs ~34GB):
        # each component is moved to GPU only while it runs.
        pipe.enable_model_cpu_offload()
        _state["pipe"] = pipe
    pipe = _state["pipe"]

    image = pipe(
        prompt,
        width=width,
        height=height,
        num_inference_steps=steps,
        guidance_scale=0.0,  # schnell is distilled; guidance is unused
    ).images[0]

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=92)
    return {
        "b64_jpeg": base64.b64encode(buf.getvalue()).decode(),
        "width": width,
        "height": height,
        "model": IMAGE_MODEL,
    }
