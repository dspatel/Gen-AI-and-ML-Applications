
import os
from huggingface_hub import hf_hub_download

token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")

repo = "meta-llama/Meta-Llama-3-8B-Instruct"
p = hf_hub_download(repo_id=repo, filename="config.json", token=token)
print("SUCCESS:", p)

