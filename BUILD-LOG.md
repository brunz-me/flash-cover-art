# Build Log — Runpod Flash Take-Home

Chronological record of every step, timing, and friction point. Source material for the presentation's friction-log section and the AI-assistance disclosure. Times are MDT.

Format per entry: what we did → result → friction/delight notes.

---

## 2026-09-17

### 1. Research pass (~20 min, AI-assisted)
- Claude Code agent read the Flash docs, blog post, `runpod/flash` + `runpod/flash-examples` repos.
- Key findings that shaped the plan: Python-only SDK (`flash init/dev/deploy`), deployed GPU workers are **Python 3.12 only**, load-balanced endpoints support arbitrary HTTP routes, **no streaming support**, 10 MB payload cap, cold start 30–60s / warm ~1–3s.
- **Demo gap identified:** none of Runpod's Flash examples serve an OpenAI-compatible API. That's our angle.
- Existing examples (avoid copying): hello world, Qwen3-TTS, SDXL image gen (docs tutorial), DistilBERT sentiment LB API, text gen with Transformers.

### 2. Concept chosen
- Rebuild the AI layer of **playlist-visuals** (real Next.js app: Spotify playlist → GPT-4o summary → DALL-E 3 cover art → upload to Spotify) on Flash.
- Flash LB endpoint serving OpenAI-compatible `/v1/chat/completions` + `/v1/images/generations` → app migrates by changing one base URL.
- Image model: FLUX.1-schnell (not SDXL — that's their tutorial territory).
- GPT-4o-vision style-analysis route stays on OpenAI → honest "incremental migration" framing.

### 3. Install CLI (~2 min)
- `uv tool install runpod-flash --python 3.12` → Flash CLI v1.19.0. 92 packages, fast, no errors.
- **Friction:** local machine runs Python 3.14 — Flash supports 3.10–3.13. Not a Flash bug, but the version window needs to be front-of-mind; error messaging for unsupported Pythons untested.

### 4. Scaffold (~1 min)
- `flash init .` in `flash-cover-art/` → generates `gpu_worker.py`, `cpu_worker.py`, `lb_worker.py`, `pyproject.toml`, `requirements.txt`, `.env.example`, README.
- **Delight:** scaffold auto-generates `AGENTS.md` with a `CLAUDE.md` symlink — Runpod is explicitly designing for AI-assisted dev. Great talking point.
- Scaffold's next-steps say `pip install -r requirements.txt` → `flash login` → `flash dev`.
- Project venv: Python 3.12.8 via uv.

### 5. Auth — NEXT
- Two options for authenticating the CLI:
  1. **`flash login`** — interactive browser flow (OAuth-style). Zero-config, good first-run DX for a human at a laptop.
  2. **`RUNPOD_API_KEY` env var** — key created in Runpod console → Settings → API Keys, set in `.env` or the shell. The right path for CI, agents, and headless environments; also what deployed workers/HTTP calls use (`Authorization: Bearer`).
- Talking point: offering both matters for a platform courting AI-assisted workflows — a coding agent can't click through a browser flow, so the API-key path is what keeps agents unblocked. (Related known issue: flash#363 — Flash can't read the `~/.runpod/config.toml` written by `runpodctl`, so the two Runpod CLIs don't share auth yet.)
- Credits were added to the account (dan.brunsdon@gmail.com) by Jessica on 9/17.

### 6. First deploy (~1 min total)
- `flash deploy` → **59s end-to-end**: pip bootstrap + 1 dep install (33s), build (7 files, 42.1 MB artifact), upload (3.8s), provision (2.1s). App + `production` env auto-created.
- Three endpoints live: `cpu_worker` + `gpu_worker` (queue, `/runsync`) and `lb_worker` (load-balanced, own subdomain with `GET /health`, `POST /process`).
- **Delight:** zero Docker, zero console clicking; CLI prints ready-to-run curl commands.

### 7. First invocation — real friction found (classifications verified against docs 2026-09-18)
- **Doc gap (verified): the kwargs-spread contract is never explained.** `gpu_worker.py` defines `gpu_hello(input_data: dict)` but the deployed handler calls `gpu_hello(**job_input)` — the `input` object is spread as keyword args. So `{"input": {"message": "..."}}` → `TypeError: unexpected keyword argument 'message'`. Correct shape: `{"input": {"input_data": {...}}}`. Verification: the deploy-apps doc *shows* this nested shape in one curl example, and apps/requests says structure "depends on your function signature" — but **no page explains the spread mechanism or the TypeError failure mode**, and the CLI's printed curl after our deploy showed plain `{"input": {}}`. Classification: PARTIALLY documented; mechanism undocumented. (Repo PR #288 acknowledges it in code, not prose.)
- **Root cause of the failed CLI curl (our diagnosis):** `-d '{"input": {}}'` → empty kwargs spread → `gpu_hello()` missing required positional arg `input_data` → worker error → 1 retry → "job timed out after 1 retries" (~40s). The CLI's own printed example fails against the CLI's own scaffold — a first-five-minutes trap. Retry semantics: NOT documented anywhere in Flash docs (no retry param exists); the timeout string comes from the underlying Serverless platform.
- Working call (warm): **delayTime 21ms, executionTime 126ms**. Cold start observed: **~11s delay** on first request. Response: H100 80GB, Python 3.12.12 (confirms the 3.12-only deployed runtime).
- **Cost observation (reclassified): documented, but the docs contradict themselves.** `GpuType.ANY` (scaffold + quickstart default) landed an **H100 80GB** for hello-world. The best-practices page explicitly says pin GPU types for production and use ANY only in dev; the pricing page says pick the smallest GPU that fits. So the guidance exists — but the quickstart/scaffold path a new dev actually walks uses ANY with no cost warning. IA finding, not a doc-absence finding.

### 7b. Docs-verification pass (2026-09-18) — honesty check before anything goes on a slide
- Ran a second research pass to classify every friction claim as documented / partially / undocumented. Corrections applied above.
- **Found: `docs.runpod.io/flash/configuration/best-practices` exists** (production config, cost optimization, pre-deployment checklist). We'll follow its checklist for the real build: pin GPU type, explicit `workers` + `idle_timeout`, health route on LB endpoint, `env=` param for worker env vars (`.env` is explicitly NOT propagated to deployed workers), local `flash dev` testing first.
- **Model caching pattern (for task 2):** documented. Storage doc: `volume=NetworkVolume(...)` mounts at `/runpod-volume/`, survives restarts, intended for sharing large models. flash-examples pattern: `NetworkVolume(name=..., size=50, datacenter=...)` + `env={"HF_HUB_CACHE": "/runpod-volume/models"}` — first cold start downloads weights once, subsequent cold starts skip. (A newer `VolumeCache` warm-cache pattern exists but is illustrative-only until the flash worker image ships runpod≥1.12.0 — worth a mention as roadmap awareness.)
- Presentation framing this enables: three distinct feedback classes — genuinely undocumented (kwargs contract, retry semantics), documented-but-contradicted-by-quickstart (GPU pinning), documented-and-good (auth defaults, best-practices checklist, volume pattern). Much stronger than a flat gripe list.

### 8. Security posture check (all passed — credit where due)
- LB endpoint **requires bearer auth by default**: `/health` → 401 without key, 200 with. No accidentally-public inference APIs.
- `flash login` writes `~/.runpod/config.toml` with **0600** perms.
- Scaffold `.gitignore` covers `.env`, `.env.local`, `.flash/`, venvs out of the box.
- Our practice: API key never echoed to terminal/logs — read from config into a shell var at call time; key never committed (only `.env.example` with placeholder ships in the repo).

## 2026-09-21

### 9. Real app built: OpenAI-compatible facade + two GPU workers
- Architecture (idiomatic Flash, per their own scaffold AGENTS.md): CPU load-balanced endpoint `openai_api` serving `/v1/chat/completions`, `/v1/images/generations`, `/v1/models`, `/health`; internally `await`s two right-sized queue workers — `chat_worker` (Qwen2.5-7B-Instruct, pinned RTX 4090 24GB) and `image_worker` (FLUX.1-schnell, pinned RTX A6000 48GB). Followed the best-practices checklist: pinned GPUs (no ANY), explicit workers/idle_timeout, `env=` for worker env vars, shared `NetworkVolume` (80GB, US_KS_2) with `HF_HUB_CACHE=/runpod-volume/models`.
- Design choices worth a slide: image route returns a **data URL** in the `url` field — self-contained, never expires (DALL-E's hosted URLs expire in hours, which the client app literally has workaround comments about); FLUX doesn't rewrite prompts so `revised_prompt` echoes the original; unsupported features (streaming, multimodal, n>1) return clear OpenAI-shaped errors instead of mystery 500s.
- **Delight:** the scaffold ships an `AGENTS.md`/`CLAUDE.md` with endpoint patterns and an agent-mistakes table — my agent followed it and avoided the documented pitfalls (module-level torch imports, deps in pyproject, hand-rolled FastAPI).
- **Friction #5: cross-file endpoint imports break the build.** `from chat_worker import chat_generate` in the LB file → build error: "endpoint 'chat_worker' is defined in multiple files... each endpoint name must be unique." The AGENTS.md says "call @Endpoint-decorated functions as if local" but doesn't mention that importing the *symbol* makes discovery double-count it. Workaround: `import chat_worker` + `chat_worker.chat_generate(...)` (module-qualified). Error message misleads — nothing is defined twice.
- `flash build`: 8 files, 6 deps, 98.8 MB artifact.

### 10. Deploying the real app — three frictions, one delight
- **Friction #6: DataCenter enum ≠ volume-capable DCs.** SDK enum offers `US_KS_2`; deploy fails ~2 min in with a 500: volumes unsupported there. The error at least lists valid DCs (good), but `US-CO-1` from that list isn't in the SDK enum — the two lists disagree in both directions. Cost: ~4 min of deploy round-trips. Fix: `US_IL_1` (in both).
- **Friction #7: worker-quota collision with stale endpoints + partial provisioning.** Second deploy failed: "Max workers across all endpoints must not exceed your workers quota (10)" — the hello-world endpoints (3×3 max workers) still counted; deploy doesn't reconcile away removed endpoints before provisioning new ones. Worse, the failed deploy had already provisioned `openai_api` — partial state. Fix: `flash undeploy <name>` × 3 (worked cleanly, `flash undeploy list` is handy), then redeploy.
- **Delight:** artifact upload hit two transient SSL errors against Cloudflare R2 and auto-retried with backoff to success — resilient by default.
- Deploy succeeded: `chat_worker`, `image_worker` (queue), `openai_api` (LB: /health, /v1/models, /v1/chat/completions, /v1/images/generations). Deploy time ~2 min incl. 98.8 MB upload.
- Smoke test: `/health` and `/v1/models` return correct payloads with bearer auth. Recurring: CLI still prints the `{"input": {}}` curl that can't work for a function with a required param.

### 11. First inference round — three findings
- **Finding A (architecture): LB gateway 502s while its backend cold-starts.** First `/v1/chat/completions` through the LB endpoint returned a bare `error code: 502` at ~36s — the gateway gave up while chat_worker was pip-installing + downloading 15GB of Qwen. The queue job itself kept running server-side and completed. Lesson for production/demo: pre-warm GPU workers after deploy (async `/run` job) or keep min workers ≥ 1; the LB tier can't mask multi-minute cold starts. Also: the 502 body is empty — no hint the backend is merely cold.
- **Finding B (my bug, interesting root cause):** `tokenizer.apply_chat_template(..., return_tensors="pt")` returns a `BatchEncoding` (not a tensor) in the deployed transformers version → `KeyError: 'shape'`. Fixed with `return_dict=True` + `encoded["input_ids"]`. Version drift between local assumptions and deployed runtime — argues for `flash deploy --preview` testing.
- **Finding C (capacity): pinned A6000 had zero capacity in US_IL_1.** Image job sat IN_QUEUE with 0 workers (not even initializing) — no error, no signal. Health endpoint (`/v2/{id}/health`) shows workers all-zero, which is the only tell. Switched to `GpuGroup.AMPERE_48` (48GB class pool). Nuance vs best-practices doc: "pin exact GPU" trades against availability; the middle ground is a VRAM-class group.
- **Friction #8: warm workers keep serving stale code after `flash deploy`.** Redeployed the chat fix; warm worker still returned the identical old traceback (delayTime 21ms — never recycled). No `flash` command exists to restart/roll workers (`flash app`/`env` have only create/get/list/delete). Options: wait out idle_timeout or kill workers in the console. A `flash app restart` (or auto-roll on deploy) is an obvious product ask.

### 12. Dependency-pinning failure — best bug of the build
- After fixing the tokenizer call and redeploying, the *same* traceback returned — from `/app/transformers/...`. Investigation of the artifact revealed the mechanism: **`flash build` resolves `dependencies=[...]` on the local machine at build time and vendors the resolved packages into the artifact** (that's what the 98.8 MB was). My `transformers>=4.44` resolved to **5.17.0** — a major version whose generation API breaks v4-era code. Nothing failed at build time; it failed at runtime on the worker.
- Fixes: pin `transformers>=4.44,<5` (and `diffusers<1`), plus version-stable two-step tokenization (`tokenize=False`, then `tokenizer(text)`).
- Lesson for the talk: on Flash, your dependency ranges are resolved *at every build* — upper-bound-pin anything with a history of breaking majors. Also explains why deps land in the artifact rather than pip-installing on the worker (faster cold starts, at artifact-size cost).
- After the pin: chat works. Cold start 17.5s + 46s model load **from volume cache** (no re-download — the volume pattern pays off), output correct OpenAI-shaped content.

### 13. The capacity spiral — and the right way out
- Image worker never scheduled: pinned A6000 in US_IL_1 → IN_QUEUE forever, zero workers, **no error surfaced anywhere** (health endpoint showing workers all-zero is the only tell). I made three sequential guesses (A6000 → AMPERE_48 group → lower min_cuda_version to 12.4) — each costing a ~2 min deploy + stale-worker wait. All wrong. **The methodical fix: query stock before deploying.** GraphQL `gpuTypes.lowestPrice(dataCenterId:...) { stockStatus }` across all volume-capable DCs showed US-IL-1 had almost nothing (one low-stock A5000!), and **EU-RO-1 is the only volume-capable DC with meaningful GPU stock** (4090/5090/L4/PRO 6000). Not a coincidence that Runpod's own flash-examples pin EU_RO_1.
- Product asks this generates: surface "0 schedulable hosts for this config" at deploy time (the API knows); a `flash` command for per-DC GPU stock; note in storage docs that a volume DC-pins your compute.
- Also confirmed live: `workers=(0,1)` yields `workersStandby: 1` in the endpoint config — open issue #364 in the wild.
- Related enum drift: `DataCenter` enum lists volume-incapable DCs (US_KS_2) while missing volume-capable ones (US-CO-1); `GpuType` enum lacks the RTX PRO 4500 Blackwell that the API reports in stock.

### 14. Moving the volume DC — blocked by an unimplemented undeploy
- Changing `NetworkVolume.datacenter` and redeploying → `NotImplementedError: NetworkVolume undeploy is not yet supported. Network volumes must be manually deleted via RunPod UI or API.` Deploy is wedged until manual deletion — and deletion requires detaching first (REST delete returns 500 "remove from all pods" while endpoints reference it).
- Working sequence: `flash undeploy chat_worker image_worker` → REST `DELETE /v1/networkvolumes/{id}` (204) → `flash deploy`. Clean deploy to EU_RO_1; FLUX switched to `enable_model_cpu_offload()` to fit the well-stocked 24/32GB consumer cards (5090/4090) instead of chasing scarce 48GB inventory.
- Recurring delight: R2 artifact upload hit transient SSL errors on 3 of 5 deploys today; retry-with-backoff saved every one.

### 15. EU-RO-1 shakeout
- Chat verified in EU-RO-1: cold 27.5s delay + 20s model load from volume cache; correct output. (First warm-up attempt failed with a *stale-build* worker — the 13:33 deploy died mid-update after uploading, and the endpoint served the old artifact until recycled. Friction #8's nastier cousin: a **failed** deploy can leave workers on inconsistent builds.)
- Observability note: job status records expire quickly — a job from ~30 min ago 404s, so capture tracebacks when they happen.
- **Friction #11: FLUX.1-schnell is a gated HF repo** — worker 401'd on download. Requires accepting the license on HF + passing a token. Doc-aligned fix: `.env` is explicitly not propagated to workers, so the token goes in via `env=` in the decorator, resolved from the build machine's environment at deploy time (never committed). Docs cover `env=` but no tutorial shows the gated-model workflow — worth a docs PR/tutorial suggestion, since Llama/FLUX/Gemma (most-wanted models) are all gated.

### 16. Gated-model token: in-place env updates don't propagate
- Added `HF_TOKEN` via `env=` and redeployed → worker still 401'd on the gated repo, **even on a fresh cold start**. Verified the token was in the build manifest and valid locally (HF whoami + direct 200 on the gated file). Endpoint config showed version bumps, but the worker template's env is not introspectable (Flash's template 404s on the REST templates API; GraphQL only lists public templates).
- **Fix: recreate the endpoint** (`flash undeploy image_worker` → `flash deploy`) — fresh endpoint 200'd immediately. Conclusion: env changes on an existing endpoint didn't reach the runtime; recreation did. Friction #12 + observability gap (can't inspect deployed env keys).
- Also: job status records expire in ~30 min, so post-mortems need tracebacks captured at failure time.

### 17. IT ALL WORKS — end-to-end numbers (2026-09-21)
- **FLUX.1-schnell on a consumer GPU (CPU offload)**: first gen 12.6s delay + 96s exec (incl. model load from volume); warm gen ~22-24s for 1024×1024. Output quality: excellent, on-prompt.
- **Full OpenAI-compatible LB round-trips**: `/v1/chat/completions` warm = **1.5s**; `/v1/images/generations` warm = **24s** (under the ~40s LB gateway timeout — but images won't survive a cold backend through the LB; keep the worker warm for demos).
- **App-level test (the actual migration proof)**: ran playlist-visuals' own `generatePlaylistSummary` + `generateArtwork` via tsx with `AI_PROVIDER=flash` — summary 6.3s, artwork 21.8s, zero changes beyond the provider switch + env vars. Artwork for synthetic "Midnight Static" trip-hop playlist: electric-blue smoke/lightning — legit cover art.
- Model-swap observation for the talk: Qwen respected the prompt's tone but blew past the "under 800 characters" instruction (GPT-4o adheres more tightly) — prompt-compliance differences are part of the real migration story; mitigations: tighter max_tokens or a truncation guard.

---

## AI-assistance ledger (for the disclosure slide)
- Claude Code (Opus) used for: platform research/doc digestion, project scaffolding driving, this build log, and pair-writing the endpoint code. All architecture decisions, the demo concept, and the presentation are Daniel's; every generated line reviewed before deploy.
