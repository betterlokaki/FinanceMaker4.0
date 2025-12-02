import os
import sys
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.getenv("GOOGLE_API_KEY")
print(API_KEY)
if not API_KEY:
    print("Error: GOOGLE_API_KEY environment variable not found.")
    print("Please set it in your terminal: export GOOGLE_API_KEY='your_key'")
    sys.exit(1)

# "Deep Research" in the API = Gemini 3 Pro + High Thinking + Search Tool
MODEL_ID = "gemini-3-pro-preview" 

def create_client():
    try:
        return genai.Client(api_key=API_KEY)
    except Exception as e:
        print(f"Failed to initialize client: {e}")
        sys.exit(1)

def chat_session():
    client = create_client()
    
    print(f"--- Connected to {MODEL_ID} (Deep Research Mode) ---")
    print("This mode enables 'High Thinking' and 'Google Search'.")
    print("Type 'quit' to exit.\n")

    chat_history = []

    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ["quit", "exit", "q"]:
                break

            print("\nGemini is researching and thinking...", end="\r")

            # 1. Configure Deep Thinking (Reasoning)
            # 2. Configure Google Search (Grounding)
            config = types.GenerateContentConfig(
                temperature=1.0, # Gemini 3 prefers 1.0
                
                # Enable Deep Thinking
                thinking_config=types.ThinkingConfig(
                    thinking_level="HIGH", # Forces deep reasoning
                    include_thoughts=True  # Returns the thought process
                ),
                
                # Enable Web Research
                tools=[types.Tool(
                    google_search=types.GoogleSearch() 
                )]
            )

            # Send request
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=chat_history + [user_input],
                config=config
            )

            # Clear loading line
            print(" " * 40, end="\r")

            # Parse and display response
            # Gemini 3 responses with thoughts come in parts: 
            # Some parts are "thoughts" (hidden reasoning), others are the final "text".
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    
                    # Display Thoughts (Reasoning/Research Process) 
                    if part.thought:
                        print(f"\n[Deep Think Process]:\n{part.text}\n")
                        print("-" * 40)
                    
                    # Display Final Answer
                    elif part.text:
                        print(f"\nGemini 3: {part.text}\n")

            # Note: For strict history management with tools/thoughts, 
            # you usually need to append the exact response objects.
            # For this simple script, we append the user prompt to keep basic context.
            chat_history.append(user_input)

        except Exception as e:
            print(f"\nError: {e}")
            print("Note: If you get a 404/400, 'gemini-3-pro-preview' might not be enabled for your API key yet.")

if __name__ == "__main__":
    chat_session()