"""Pre-download the two local models ClaimsRAG needs.

The app deliberately forces offline mode (HF_HUB_OFFLINE=1 in backend/app.py),
so on a fresh machine the models must already be in the Hugging Face cache or
the server refuses to start. Run this once after installing requirements:
`.venv/bin/python scripts/precache_models.py`
"""
from sentence_transformers import CrossEncoder, SentenceTransformer

MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
]

if __name__ == "__main__":
    for name in MODELS:
        print(f"Pre-caching {name} ...", flush=True)
        if name.startswith("cross-encoder"):
            CrossEncoder(name)
        else:
            SentenceTransformer(name)
    print("Model cache ready — the app can now start in offline mode.")
