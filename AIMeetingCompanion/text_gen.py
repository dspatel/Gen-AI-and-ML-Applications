from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
# Replace with your desired Llama 2 model ID
model_id = "meta-llama/Llama-2-7b-chat-hf"
access_token = "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"
# Load the tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_id,token=access_token,)

# Load the model
model = AutoModelForCausalLM.from_pretrained(model_id,token=access_token,)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device set to use {device}")
model = model.to(device)
# Create a pipeline for text generation
pipe = pipeline("text-generation", model=model, tokenizer=tokenizer
                , torch_dtype=torch.float16, device_map="auto")

# Example prompt
prompt = "Write a poem about a cat."

# Generate text
results = pipe(prompt, num_return_sequences=1, max_length=150, do_sample=True
               , top_k=50)[0]["generated_text"]

# Print the generated text
print(results)