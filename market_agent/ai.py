import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from market_agent.config import Settings


def complete(settings: Settings, prompt: str, role: str = "writer") -> str:
    # If OpenAI is configured, we can leverage both local Ollama and cloud OpenAI in parallel for dual-precision synthesis!
    if settings.openai_api_key:
        local_model = settings.local_writer_model if role == "writer" else settings.local_helper_model
        print(f"Initiating parallel dual-AI generation: Local ({local_model}) and Cloud ({settings.openai_model})...")
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_local = executor.submit(_ollama, settings, prompt, local_model)
            future_cloud = executor.submit(_openai, settings, prompt)
            
            local_res = future_local.result()
            cloud_res = future_cloud.result()
            
        # Cleanly merge or format both outputs to present a highly precise, multi-perspective final report
        lines = []
        if local_res and "AI summary unavailable" not in local_res:
            lines.append("🤖 LOCAL AI ANALYSIS (Ollama):\n" + local_res)
        else:
            lines.append(f"🤖 LOCAL AI ANALYSIS (Ollama): Unavailable ({local_res})")
            
        if cloud_res and "AI summary unavailable" not in cloud_res:
            lines.append("☁️ CLOUD AI ANALYSIS (OpenAI):\n" + cloud_res)
        else:
            lines.append(f"☁️ CLOUD AI ANALYSIS (OpenAI): Unavailable ({cloud_res})")
            
        return "\n\n".join(lines)
        
    return _ollama(settings, prompt, settings.local_writer_model if role == "writer" else settings.local_helper_model)



def _ollama(settings: Settings, prompt: str, model: str) -> str:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{settings.ollama_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            data = json.loads(res.read().decode("utf-8"))
        return data.get("response", "").strip()
    except Exception as exc:
        return f"AI summary unavailable. Reason: {exc}"


def _openai(settings: Settings, prompt: str) -> str:
    payload = json.dumps(
        {
            "model": settings.openai_model,
            "messages": [
                {"role": "system", "content": "You are an Indian stock market analyst. Use simple English, one line per insight, no unsupported claims."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.openai_api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as res:
            data = json.loads(res.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"AI summary unavailable. Reason: {exc}"
