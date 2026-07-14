import os
import sys
from google import genai
from google.genai import types

def main():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("Error: No GEMINI_API_KEY found.")
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    model_name = 'gemini-3.5-flash'
    prompt = "What version of gemini are you?"

    print(f"Sending request to {model_name}...")
    print(f"Prompt: '{prompt}'")

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )

        if response and response.text:
            print(f"\nResponse received:\n{response.text.strip()}")
        else:
            print("\nReceived empty response.")

    except Exception as e:
        print(f"\nError occurred: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
