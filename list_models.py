import os
from google import genai

def list_gemini_models():
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("No API key available to list models.")
        return
    client = genai.Client(api_key=api_key)
    try:
        models = client.models.list()
        for m in models:
            print(m.name, m.supported_actions)
    except Exception as e:
        print(f"Error listing models: {e}")

list_gemini_models()
