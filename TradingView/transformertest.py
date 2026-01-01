
import os
from huggingface_hub import hf_hub_download

token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
print("Token present:", bool(token))

repo = "meta-llama/Meta-Llama-3-8B-Instruct"

# tokenizer_config.json is a good “tokenizer exists” check
p = hf_hub_download(repo_id=repo, filename="tokenizer_config.json", token=token)
print("SUCCESS:", p)
