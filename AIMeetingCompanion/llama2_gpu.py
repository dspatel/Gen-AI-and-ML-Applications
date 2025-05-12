'''Quantization techniques reduces memory and computational costs by representing weights and activations with lower-precision data types like 8-bit integers (int8). 
This enables loading larger models you normally wouldn’t be able to fit into memory, and speeding up inference. Transformers supports the AWQ and GPTQ quantization algorithms 
and it supports 8-bit and 4-bit quantization with bitsandbytes'''

import tran
from torch import cuda, bfloat16
from transformers import BitsAndBytesConfig

bnb_config = transformers.BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type='nf4',
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=bfloat16
)