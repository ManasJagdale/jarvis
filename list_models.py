"""
list_models.py

Run this any time you get a 404/model-not-found error. It asks the Gemini
API directly which models your key can actually use right now, instead of
guessing from docs or old blog posts that go stale fast.

Usage:
    python list_models.py
"""

from google import genai

from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

print("Models available to your API key that support generateContent:\n")
for model in client.models.list():
    if "generateContent" in (model.supported_actions or []):
        print(f"  {model.name}")
