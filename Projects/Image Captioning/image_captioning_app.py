import gradio as gr
import numpy as np
from PIL import Image
from transformers import AutoProcessor, BlipForConditionalGeneration

#Load the pretrained model
processor = AutoProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")


#Function that takes an inpyt image and generates a caption for it
def caption_image(input_image: np.array):
    #Convert numpy array to PIL image and convert to RGB
    raw_image = Image.fromarray(input_image).convert("RGB")

    #Process the image
    input = processor(raw_image, return_tensors="pt")
    #Generate caption
    output = model.generate(**input, max_length=50)
    #Decode the generated tokens to text
    caption = processor.decode(output[0], skip_special_tokens=True)
    return caption

iface = gr.Interface(
    fn=caption_image,
    inputs=gr.Image(),
    outputs="text",
    title="Image Captioning",
    description="Upload an image to generate a caption.",
    theme="default",
)

iface.launch(server_name="127.0.0.1", server_port= 7860)