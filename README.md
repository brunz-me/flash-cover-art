# flash-cover-art

**An OpenAI-compatible API for open models, built on [Runpod Flash](https://docs.runpod.io/flash/quickstart) — so an app written against OpenAI migrates to self-hosted GPUs by changing one base URL.**

Built as a working demo: **playlist-visuals** (my Next.js app that turns Spotify playlists into AI cover art with GPT-4o + DALL-E 3) now runs on Qwen2.5-7B + FLUX.1-schnell on Runpod Serverless — with **zero changes to its request/response handling**. The migration was a provider block in one file plus three env vars.

```
                        ┌─────────────────────────────────────────────┐
                        │                Runpod Flash                 │
 playlist-visuals       │  ┌───────────────┐                          │
 (or any OpenAI client) │  │  openai_api   │   await   ┌────────────┐ │
 ──────────────────────►│  │  CPU workers  ├──────────►│chat_worker │ │
  base URL:             │  │  /v1/chat/…   │           │ Qwen2.5-7B │ │
  https://{id}.api      │  │  /v1/images/… │           │ RTX 4090   │ │
  .runpod.ai/v1         │  │  /v1/models   │   await   ├────────────┤ │
                        │  │  /health      ├──────────►│image_worker│ │
                        │  └───────────────┘           │ FLUX.1-    │ │
                        │        network volume ◄──────│ schnell    │ │
                        │        (HF model cache)      │ 4090/5090  │ │
                        │                              └────────────┘ │
                        └─────────────────────────────────────────────┘
```

## Why this shape

- **Load-balanced CPU endpoint** serves the HTTP API (cheap, always warm) and `await`s the GPU workers — Flash handles remote dispatch, scaling, and bearer-token auth (requests 401 before your code runs).
- **Queue-based GPU workers** are right-sized per model: Qwen2.5-7B on a 24 GB RTX 4090; FLUX.1-schnell fits consumer cards via `enable_model_cpu_offload()`.
- **A shared network volume** caches HuggingFace weights: the first cold start downloads, every one after skips it.
- **Data-URL image responses**: generated covers come back as self-contained `data:` URLs — no object storage, no expiring links (DALL-E hosted URLs die within hours).

## Run it

```bash
uv tool install runpod-flash        # Python 3.10–3.13 on your machine
flash login                          # or RUNPOD_API_KEY env var

# FLUX.1-schnell is a gated repo: accept the license on HuggingFace, then
cp .env.example .env                 # add HF_TOKEN=hf_... (never committed)

flash deploy
```

Then point any OpenAI client at it:

```bash
curl -X POST https://{lb-endpoint-id}.api.runpod.ai/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":50}'
```

Or in an app:

```ts
const base = process.env.FLASH_BASE_URL; // https://{id}.api.runpod.ai/v1
await fetch(`${base}/chat/completions`, { ... });   // unchanged OpenAI shape
await fetch(`${base}/images/generations`, { ... }); // unchanged OpenAI shape
```

## Measured performance (EU-RO-1, Sept 2026)

| Operation | Cold | Warm |
|---|---|---|
| Chat completion (Qwen2.5-7B, RTX 4090) | ~48s (incl. volume model load) | **1.5s** |
| Image generation (FLUX.1-schnell 1024², 4 steps) | ~110s first-ever (download) | **22–24s** |
| Deploy (build → live endpoints) | — | ~2 min |

## Honest limits

| Not supported | Behavior |
|---|---|
| `stream: true` | Clear error (Flash LB endpoints don't stream today) |
| Multimodal message content | Clear error — vision routes stay on your original provider |
| `n > 1` | Clear error |
| Cold image backend via the LB | The gateway times out (~40s) before a cold FLUX worker finishes loading — pre-warm with a queue `/run` call, or keep `workers=(1, n)` |

## Files

| File | What |
|---|---|
| `api.py` | The OpenAI-compatible facade (LB endpoint, CPU) |
| `chat_worker.py` | Qwen2.5-7B-Instruct queue worker (GPU) |
| `image_worker.py` | FLUX.1-schnell queue worker (GPU) |
| `resources.py` | Shared network volume, datacenter, env |
| `BUILD-LOG.md` | Chronological build journal incl. every friction point hit and how it was resolved |

## The friction log

The full journal is in [BUILD-LOG.md](BUILD-LOG.md) — every step from `flash init` to working app, including the failures: the kwargs-spread TypeError on the scaffold's own hello-world, dependency pinning (`transformers>=4.44` silently resolving to a breaking 5.x at build time), GPU capacity vs. datacenter-pinned volumes (query stock via the API before picking a DC — don't guess), gated-model tokens via `env=`, and stale workers surviving deploys.

## AI assistance disclosure

This project was built pair-programming with Claude Code (Anthropic). The agent drove research, scaffolding, code iteration, and the build log; architecture decisions, model selection, and all verification were human-reviewed. Details in the AI-assistance ledger at the bottom of [BUILD-LOG.md](BUILD-LOG.md).
