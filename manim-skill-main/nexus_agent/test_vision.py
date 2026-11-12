"""Test vision capability of configured Nexus model."""

import base64
import io
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

def test_nexus_vision():
    load_dotenv()
    api_url = os.getenv("NEXUS_API_URL", "https://nexusapi.navigatelabs.ai/v1").rstrip("/")
    api_key = os.getenv("NEXUS_API_KEY", "")
    model = os.getenv("NEXUS_MODEL", "")

    if not api_key:
        print("ERROR: NEXUS_API_KEY is not set.")
        sys.exit(1)

    print(f"Testing model '{model}' at '{api_url}' for vision capability...")

    # Create a small 50x50 pure red image in memory
    img = Image.new("RGB", (50, 50), color=(255, 0, 0))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64_img = base64.b64encode(buffer.getvalue()).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_img}"

    client = OpenAI(base_url=api_url, api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this image. What is the dominant color? Reply with just one word."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=20,
        )
        content = response.choices[0].message.content
        print(f"SUCCESS: Vision request succeeded. Response: {content.strip()}")
        return True
    except Exception as e:
        # Sanitize any accidental key leak in error
        err_msg = str(e).replace(api_key, "[REDACTED]")
        print(f"FAILURE: Vision request failed: {err_msg}")
        return False

if __name__ == "__main__":
    success = test_nexus_vision()
    sys.exit(0 if success else 1)

