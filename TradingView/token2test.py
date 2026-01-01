
import os
from huggingface_hub import hf_hub_download

token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
print("Token found:", bool(token))

for repo in [
    "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "meta-llama/Meta-Llama-3-8B-Instruct",
]:
    try:
        p = hf_hub_download(repo_id=repo, filename="config.json", token=token)
        print("OK:", repo)
    except Exception as e:
        print("FAIL:", repo, "->", type(e).__name__, e)

