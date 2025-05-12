from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

# Load the pre-trained model and tokenizer
model_name = "facebook/blenderbot-400M-distill"

model = AutoModelForSeq2SeqLM.from_pretrained(model_name) # This loads the model and returns a dictionary of the model
tokenizer = AutoTokenizer.from_pretrained(model_name)

# Tracking conversation history
conversation_history = []

# At every step we pass the conversation history along with the new user input to the model


while True:
    
    #Fetch user input
    input_text = input("User: ") # This is the user input and it is a string
    if input_text.lower() == "exit": # This is the exit condition for the loop
        break

    history_string = "\n".join(conversation_history) 
    # Tokenize the input text and conversation history
    inputs = tokenizer.encode(history_string, input_text, return_tensors="pt") #this creates a tensor which is a python dictionary that can be passed to the model


    # Generate a response from the model
    outputs = model.generate(inputs) # This model response is a dictionary and not words in plain text
    print(outputs)

    # Decode the generated response
    response = tokenizer.decode(outputs[0], skip_special_tokens=True).strip() # This decodes the model response to a string

    print(response)

    # Append the user input and model response to the conversation history
    conversation_history.append(input_text)
    conversation_history.append(response)
    #print(conversation_history)