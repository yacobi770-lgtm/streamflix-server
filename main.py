from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import urllib.parse

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return FileResponse("index.html")

class TranslateRequest(BaseModel):
    text: str
    from_lang: str = "en"
    to: str = "he"

    class Config:
        populate_by_name = True

@app.post("/api/translate")
async def translate(req: TranslateRequest):
    text = req.text
    from_lang = req.from_lang
    to = req.to

    if not text or not text.strip():
        return {"result": text}

    # Google Translate API
    try:
        tl = "iw" if to == "he" else to
        encoded = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={from_lang}&tl={tl}&dt=t&q={encoded}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            d = r.json()
            if d and d[0]:
                result = "".join(x[0] for x in d[0] if x and x[0])
                if result and result != text:
                    return {"result": result.strip()}
    except Exception as e:
        pass

    # MyMemory fallback
    try:
        encoded = urllib.parse.quote(text)
        url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair={from_lang}|{to}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url)
            d = r.json()
            t = d.get("responseData", {}).get("translatedText", "")
            if t and "MYMEMORY" not in t and t != text:
                return {"result": t}
    except Exception:
        pass

    return {"result": text}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
