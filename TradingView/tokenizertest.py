
import os
from transformers import AutoTokenizer

token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN")
tok = AutoTokenizer.from_pretrained("meta-llama/Meta-Llama-3-8B-Instruct", use_fast=False, token=token)
print("Tokenizer loaded:", type(tok))

