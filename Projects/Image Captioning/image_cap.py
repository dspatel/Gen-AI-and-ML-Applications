'''
"Blip2Processor" and "Blip2ForConditionalGeneration" are components of the BLIP model, which is a vision-language model available in the Hugging Face Transformers library.

AutoProcessor : This is a processor class that is used for preprocessing data for the BLIP model. It wraps a BLIP image processor and an OPT/T5 tokenizer into a single processor. This means it can handle both image and text data, preparing it for input into the BLIP model.

Note: A tokenizer is a tool in natural language processing that breaks down text into smaller, manageable units (tokens), such as words or phrases, enabling models to analyze and understand the text.

BlipForConditionalGeneration : This is a model class that is used for conditional text generation given an image and an optional text prompt. In other words, it can generate text based on an input image and an optional piece of text. This makes it useful for tasks like image captioning or visual question answering, where the model needs to generate text that describes an image or answer a question about an image.

'''

import requests
from PIL import Image
from transformers import AutoProcessor, BlipForConditionalGeneration
# Load the pretrained processor and model
processor = AutoProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")

#In the next phase, you fetch an image, which will be captioned by your pre-trained model. This image can either be a local file or fetched from a URL. The Python Imaging Library, PIL,
# is used to open the image file and convert it into an RGB format which is suitable for the model.

# Load your image, DONT FORGET TO WRITE YOUR IMAGE NAME
img_path = "IMG_0122.jpg"
# convert it into an RGB format 
image = Image.open(img_path).convert('RGB')
# If you want to use a URL instead of a local image, uncomment the following lines:
# url = 'https://storage.googleapis.com/sfr-vision-language-research/BLIP/demo.jpg'
# image = Image.open(requests.get(url, stream=True).raw).convert('RGB')

#Next, the pre-processed image is passed through the processor to generate inputs in the required format. The return_tensors argument is set to "pt" to return PyTorch tensors.
text = "the image of"
inputs = processor(images=image, text=text, return_tensors="pt")

#You then pass these inputs into your model's generate method. The argument max_new_tokens=50 specifies that the model should generate a caption of up to 50 tokens in length.
#The two asterisks (**) in Python are used in function calls to unpack dictionaries and pass items in the dictionary as keyword arguments to the function.
#  **inputs is unpacking the inputs dictionary and passing its items as argumen


# Generate a caption for the image
outputs = model.generate(**inputs, max_length=50)

#Finally, the generated output is a sequence of tokens. To transform these tokens into human-readable text, you use the decode method provided by the processor. 
# The skip_special_tokens argument is set to True to ignore special tokens in the output text.

# Decode the generated tokens to text
caption = processor.decode(outputs[0], skip_special_tokens=True)
# Print the caption
print(caption)
## ts to the model.