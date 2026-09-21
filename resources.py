# Shared infrastructure resources for all workers.
#
# One network volume caches HuggingFace model weights across cold starts
# (documented pattern: docs.runpod.io/flash/configuration/storage).
# First cold start downloads weights to the volume; every cold start after
# that skips the download. Both GPU workers pin the same datacenter so they
# can mount the same volume.
from runpod_flash import NetworkVolume
from runpod_flash.core.resources.datacenter import DataCenter

# DC choice is load-bearing: the volume pins every attached worker to this
# DC's GPU inventory. US_IL_1 (first pick) supports volumes but had almost
# zero GPU stock — jobs queued forever with no error. Checked stock via the
# GraphQL API across all volume-capable DCs: EU_RO_1 has the deepest pool
# (4090/5090/L4/PRO 6000). It's also the DC Runpod's own flash-examples use.
DATACENTER = DataCenter.EU_RO_1

# ~15 GB (Qwen2.5-7B) + ~34 GB (FLUX.1-schnell) + headroom.
HF_CACHE_VOLUME = NetworkVolume(name="hf-cache", size=80, datacenter=DATACENTER)

# Deployed workers do NOT inherit .env — env must be passed explicitly
# (docs.runpod.io/flash/configuration/parameters). Values here are resolved
# on the build machine at deploy time; HF_TOKEN comes from the local
# environment / .env (gitignored) and never appears in the repo.
# FLUX.1-schnell is a gated repo, so the image worker needs the token.
from runpod_flash import load_dotenv
import os

load_dotenv()

HF_ENV = {"HF_HUB_CACHE": "/runpod-volume/models"}
if os.getenv("HF_TOKEN"):
    HF_ENV["HF_TOKEN"] = os.environ["HF_TOKEN"]
