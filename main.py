from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from cachetools import TTLCache

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

stream_cache = TTLCache(maxsize=1024, ttl=43200)
@app.get("/api/anime/stream/{episode_id}")
async def get_stream_link(episode_id: str):
    if episode_id in stream_cache:
        return {"stream_url": stream_cache[episode_id]}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(f"https://aniplus.co/episode/{episode_id}", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("iframe", state="visible", timeout=15000)
            iframe = await page.query_selector("iframe")
            src = await iframe.get_attribute("src")
            stream_cache[episode_id] = src
            return {"stream_url": src}
        except Exception:
            raise HTTPException(status_code=500, detail="Error")
        finally:
            await browser.close()
