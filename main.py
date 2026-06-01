from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from playwright.async_api import async_playwright
from cachetools import TTLCache
import uvicorn
import httpx
import json
import re

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/manifest.json")
async def manifest():
    return FileResponse("manifest.json")

@app.get("/sw.js")
async def sw():
    return FileResponse("sw.js")

stream_cache = TTLCache(maxsize=1024, ttl=43200)
subtitle_cache = TTLCache(maxsize=512, ttl=86400)

# ═══ SUBTITLE PROXY ═══
OPENSUBTITLES_KEY = "uXMNABAThAoHpgZhdq6j4ncNifNNUmp5"

@app.get("/api/subtitles/{imdb_id}")
async def get_subtitles(imdb_id: str, title: str = "", lang: str = "he"):
    """Get Hebrew subtitles only - tries all Israeli and international sources"""
    cache_key = f"sub_{imdb_id}_{title}"
    if cache_key in subtitle_cache:
        return JSONResponse(subtitle_cache[cache_key])

    result = {"url": None, "source": None}

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        
        # Source 1: OpenSubtitles v3 API - Hebrew
        try:
            clean_id = imdb_id.replace("tt", "")
            r = await client.get(
                f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={clean_id}&languages=he&order_by=download_count",
                headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"}
            )
            data = r.json()
            items = data.get("data", [])
            if items:
                file_id = items[0].get("attributes", {}).get("files", [{}])[0].get("file_id")
                if file_id:
                    r2 = await client.post(
                        "https://api.opensubtitles.com/api/v1/download",
                        headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"},
                        json={"file_id": file_id}
                    )
                    d2 = r2.json()
                    if d2.get("link"):
                        result = {"url": d2["link"], "source": "OpenSubtitles"}
        except Exception as e:
            pass

        # Source 2: SubDL - Hebrew
        if not result["url"]:
            try:
                params = {"api_key": "free", "languages": "HE"}
                if imdb_id:
                    params["imdb_id"] = imdb_id
                elif title:
                    params["film_name"] = title
                r = await client.get("https://api.subdl.com/api/v1/subtitles", params=params)
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    url = f"https://dl.subdl.com{subs[0]['url']}"
                    result = {"url": url, "source": "SubDL"}
            except Exception:
                pass

        # Source 3: OpenSubtitles REST (old) - Hebrew
        if not result["url"] and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                r = await client.get(
                    f"https://rest.opensubtitles.org/search/imdbid-{clean_id}/sublanguageid-heb",
                    headers={"X-User-Agent": "StreamFlix v3.0"}
                )
                data = r.json()
                if isinstance(data, list) and data:
                    best = sorted(data, key=lambda x: int(x.get("SubDownloadsCnt", 0)), reverse=True)[0]
                    if best.get("SubDownloadLink"):
                        result = {"url": best["SubDownloadLink"], "source": "OpenSubtitles REST"}
            except Exception:
                pass

        # Source 4: Subscene scraping - Hebrew
        if not result["url"] and title:
            try:
                r = await client.get(
                    f"https://subscene.com/subtitles/searchbytitle?query={title}&l=23",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                html = r.text
                # Find first Hebrew subtitle link
                matches = re.findall(r'href="(/subtitles/[^"]+)"[^>]*>[^<]*</a>', html)
                for match in matches[:3]:
                    try:
                        sub_r = await client.get(
                            f"https://subscene.com{match}",
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        dl_match = re.search(r'href="(/subtitle/download[^"]*)"', sub_r.text)
                        if dl_match:
                            dl_url = f"https://subscene.com{dl_match.group(1)}"
                            result = {"url": dl_url, "source": "Subscene"}
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # Source 5: YifySubtitles - Hebrew
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://yifysubtitles.ch/movie-imdb/{imdb_id}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                html = r.text
                he_match = re.search(r'href="(/subtitles/[^"]*)"[^>]*>\s*[^<]*[Hh]ebrew', html)
                if he_match:
                    sub_r = await client.get(
                        f"https://yifysubtitles.ch{he_match.group(1)}",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    dl_match = re.search(r'href="(/subtitle/[^"]*\.zip)"', sub_r.text)
                    if dl_match:
                        result = {"url": f"https://yifysubtitles.ch{dl_match.group(1)}", "source": "YifySubtitles"}
            except Exception:
                pass

        # Source 5: Wizdom (Israeli) - Hebrew
        if not result["url"] and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                r = await client.get(
                    f"https://wizdom.xyz/api/files?action=index&imdb={clean_id}&json=1",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                if isinstance(data, list) and data:
                    # Sort by downloads
                    best = sorted(data, key=lambda x: x.get("downloads", 0), reverse=True)
                    for sub in best[:3]:
                        sub_id = sub.get("id")
                        if sub_id:
                            dl_url = f"https://wizdom.xyz/api/files?action=download&type=sub&id={sub_id}"
                            result = {"url": dl_url, "source": "Wizdom"}
                            break
            except Exception:
                pass

        # Source 5b: Ktuvit (Israeli) - Hebrew
        if not result["url"] and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                r = await client.get(
                    f"https://www.ktuvit.me/Services/MoovieService.asmx/SearchMovieByImdb?imdb={clean_id}",
                    headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
                )
                data = r.json()
                if data.get("d"):
                    movie_id = data["d"].get("MovieID")
                    if movie_id:
                        r2 = await client.get(
                            f"https://www.ktuvit.me/Services/MoovieService.asmx/GetMovieSubtitleList?movieID={movie_id}&numOfSubs=10",
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        subs = r2.json().get("d", [])
                        if subs:
                            sub_id = subs[0].get("SubtitleID")
                            if sub_id:
                                result = {"url": f"https://www.ktuvit.me/Services/DownloadFile.ashx?subtitleID={sub_id}", "source": "Ktuvit"}
            except Exception:
                pass

        # Source 5c: Podnapisi - Hebrew
        if not result["url"] and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                r = await client.get(
                    f"https://www.podnapisi.net/subtitles/search/advanced?keywords=&imdb={clean_id}&language=he&format=json",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    best = sorted(subs, key=lambda x: x.get("downloads_count", 0), reverse=True)
                    for sub in best[:3]:
                        sub_id = sub.get("id")
                        if sub_id:
                            result = {"url": f"https://www.podnapisi.net/subtitles/{sub_id}/download", "source": "Podnapisi"}
                            break
            except Exception:
                pass

        # Source 6: Heb Subs API (aggregates Wizdom + OpenSubtitles + Ktuvit)
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://stremio-heb-subs.onrender.com/subtitles/movie/{imdb_id}.json",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    best = sorted(subs, key=lambda x: x.get("id", ""), reverse=False)
                    for sub in best[:3]:
                        url = sub.get("url", "")
                        if url:
                            result = {"url": url, "source": "HebSubs"}
                            break
            except Exception:
                pass

        # Source 6b: Sratim (Israeli movie subtitles)
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://www.sratim.co.il/search.php?q={imdb_id}&dce=1",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                html = r.text
                # Find movie page
                movie_match = re.search(r'href="(movie\.php\?id=\d+)"', html)
                if movie_match:
                    movie_r = await client.get(
                        f"https://www.sratim.co.il/{movie_match.group(1)}&part=subtitles&lang=1",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    sub_match = re.search(r'href="(downloadsubtitle\.php\?id=\d+[^"]*)"', movie_r.text)
                    if sub_match:
                        result = {"url": f"https://www.sratim.co.il/{sub_match.group(1)}", "source": "Sratim"}
            except Exception:
                pass

        # Source 6c: Machi subtitles
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://machi.co.il/subtitles/?imdb={imdb_id}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", []) if isinstance(data, dict) else []
                if subs:
                    url = subs[0].get("download_url", "")
                    if url:
                        result = {"url": url, "source": "Machi"}
            except Exception:
                pass

        # Source 7: Subscenter (Israeli) - Hebrew
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://www.subscenter.org/he/subtitle/search/?q={imdb_id}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                html = r.text
                match = re.search(r'href="(/he/subtitle/movie/[^"]+)"', html)
                if match:
                    sub_r = await client.get(
                        f"https://www.subscenter.org{match.group(1)}",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    dl = re.search(r'href="(/he/subtitle/download/[^"]+)"', sub_r.text)
                    if dl:
                        result = {"url": f"https://www.subscenter.org{dl.group(1)}", "source": "Subscenter"}
            except Exception:
                pass

        # Source 7b: Addic7ed - Hebrew (TV shows)
        if not result["url"] and title:
            try:
                r = await client.get(
                    f"https://www.addic7ed.com/search.php?search={title}&Submit=Search",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.addic7ed.com"}
                )
                html = r.text
                match = re.search(r'href="(/show/\d+)"', html)
                if match:
                    show_r = await client.get(
                        f"https://www.addic7ed.com{match.group(1)}",
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.addic7ed.com"}
                    )
                    dl = re.search(r'href="(/original/[^"]+/Hebrew[^"]*)"', show_r.text)
                    if dl:
                        result = {"url": f"https://www.addic7ed.com{dl.group(1)}", "source": "Addic7ed"}
            except Exception:
                pass

        # Source 8: Torec.net (Israeli) - Hebrew only
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://www.torec.net/ajax/sub/newsearch.asp?sub_name={imdb_id}&s=all",
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.torec.net"}
                )
                data = r.json() if r.headers.get('content-type','').find('json') >= 0 else None
                if data and isinstance(data, list) and data:
                    sub_id = data[0].get("sub_id") or data[0].get("id")
                    if sub_id:
                        result = {"url": f"https://www.torec.net/ajax/sub/downloadun.asp?sub_id={sub_id}", "source": "Torec"}
                else:
                    # Try HTML scraping
                    r2 = await client.get(
                        f"https://www.torec.net/s.asp?s={imdb_id}",
                        headers={"User-Agent": "Mozilla/5.0"}
                    )
                    match = re.search(r'href="(/sub\.asp\?s=\d+)"', r2.text)
                    if match:
                        sub_r = await client.get(
                            f"https://www.torec.net{match.group(1)}",
                            headers={"User-Agent": "Mozilla/5.0"}
                        )
                        dl = re.search(r'href="(/ajax/sub/downloadun\.asp[^"]+)"', sub_r.text)
                        if dl:
                            result = {"url": f"https://www.torec.net{dl.group(1)}", "source": "Torec"}
            except Exception:
                pass

        # Source 8b: ScrewZira - Hebrew only
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://www.screwzira.com/API/Search",
                    params={"FilmName": title, "Version": "", "lang": "he"},
                    headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
                )
                data = r.json()
                subs = data.get("Subtitles", []) if isinstance(data, dict) else []
                if subs:
                    sub_id = subs[0].get("Identifier", "")
                    if sub_id:
                        result = {"url": f"https://www.screwzira.com/API/Download?Identifier={sub_id}", "source": "ScrewZira"}
            except Exception:
                pass

        # Source 9: Heb Subs Premium API (aggregates Wizdom+OpenSubtitles+Ktuvit+Machi)
        if not result["url"] and imdb_id:
            try:
                # Try movie
                r = await client.get(
                    f"https://stremiohebsubs.onrender.com/subtitles/movie/{imdb_id}.json",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    for sub in subs[:5]:
                        url = sub.get("url", "")
                        if url and "he" in sub.get("id","").lower():
                            result = {"url": url, "source": "HebSubsPremium"}
                            break
                    if not result["url"] and subs:
                        result = {"url": subs[0]["url"], "source": "HebSubsPremium"}
            except Exception:
                pass

        # Source 9b: Heb Subs Premium for TV series
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://stremiohebsubs.onrender.com/subtitles/series/{imdb_id}:1:1.json",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    result = {"url": subs[0]["url"], "source": "HebSubsPremium-TV"}
            except Exception:
                pass

        # Source 9c: Ktuvit Stremio addon
        if not result["url"] and imdb_id:
            try:
                r = await client.get(
                    f"https://4b139a4b7f94-ktuvit-stremio.baby-beamup.club/subtitles/movie/{imdb_id}.json",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    result = {"url": subs[0]["url"], "source": "KtuvitStremio"}
            except Exception:
                pass

        # Source 10: OpenSubtitles English → translate (always try if no Hebrew found)
        en_url = None
        if not result["url"] and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                # Try English
                r = await client.get(
                    f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={clean_id}&languages=en&order_by=download_count",
                    headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"}
                )
                data = r.json()
                items = data.get("data", [])
                if items:
                    file_id = items[0].get("attributes", {}).get("files", [{}])[0].get("file_id")
                    if file_id:
                        r2 = await client.post(
                            "https://api.opensubtitles.com/api/v1/download",
                            headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"},
                            json={"file_id": file_id}
                        )
                        d2 = r2.json()
                        if d2.get("link"):
                            en_url = d2["link"]
            except Exception:
                pass

        # Source 10b: SubDL Korean/Japanese (for Asian dramas) → translate
        if not result["url"] and not en_url and imdb_id:
            try:
                # Try Korean first
                r = await client.get(f"https://api.subdl.com/api/v1/subtitles?api_key=free&imdb_id={imdb_id}&languages=KO")
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    en_url = f"https://dl.subdl.com{subs[0]['url']}"
                    result = {"url": en_url, "source": "SubDL-KO", "needs_translation": True, "from_lang": "ko"}
            except Exception:
                pass

        # Source 10c: OpenSubtitles Korean
        if not result["url"] and not en_url and imdb_id:
            try:
                clean_id = imdb_id.replace("tt", "")
                r = await client.get(
                    f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={clean_id}&languages=ko&order_by=download_count",
                    headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"}
                )
                data = r.json()
                items = data.get("data", [])
                if items:
                    file_id = items[0].get("attributes", {}).get("files", [{}])[0].get("file_id")
                    if file_id:
                        r2 = await client.post(
                            "https://api.opensubtitles.com/api/v1/download",
                            headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"},
                            json={"file_id": file_id}
                        )
                        d2 = r2.json()
                        if d2.get("link"):
                            en_url = d2["link"]
                            result = {"url": en_url, "source": "OpenSubtitles-KO", "needs_translation": True, "from_lang": "ko"}
            except Exception:
                pass

        # Source 10d: SubDL English fallback
        if not result["url"] and not en_url and imdb_id:
            try:
                r = await client.get(f"https://api.subdl.com/api/v1/subtitles?api_key=free&imdb_id={imdb_id}&languages=EN")
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    en_url = f"https://dl.subdl.com{subs[0]['url']}"
            except Exception:
                pass

        if not result["url"] and en_url:
            result = {"url": en_url, "source": "OpenSubtitles-EN", "needs_translation": True}

    if result["url"]:
        subtitle_cache[cache_key] = result

    return JSONResponse(result)


@app.get("/api/subtitle-content")
async def get_subtitle_content(url: str):
    """Proxy subtitle file content to avoid CORS"""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            content = r.content
            
            # If zip file, extract first SRT/VTT
            if url.endswith('.zip') or 'zip' in r.headers.get('content-type',''):
                import io, zipfile
                z = zipfile.ZipFile(io.BytesIO(content))
                for name in z.namelist():
                    if name.endswith(('.srt','.vtt','.ass','.ssa')):
                        content = z.read(name)
                        break
            
            return JSONResponse({
                "content": content.decode('utf-8', errors='replace'),
                "encoding": "utf-8"
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/translate-srt")
async def translate_srt_file(request: dict):
    import urllib.parse
    srt_text = request.get("srt", "")
    from_lang = request.get("from_lang", "auto")  # auto, en, ko, ja, zh
    if not srt_text:
        return JSONResponse({"translated": ""})
    
    lines = srt_text.split("\n")
    text_lines = []
    line_map = []
    
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\d+$", stripped) or "-->" in stripped or not stripped:
            line_map.append(None)
        else:
            line_map.append(len(text_lines))
            text_lines.append(stripped)
    
    if not text_lines:
        return JSONResponse({"translated": srt_text})
    
    translated_lines = list(text_lines)
    
    async with httpx.AsyncClient(timeout=60) as client:
        chunk_size = 80
        for i in range(0, len(text_lines), chunk_size):
            chunk = text_lines[i:i+chunk_size]
            joined = "\n".join(chunk)
            try:
                encoded = urllib.parse.quote(joined)
                r = await client.get(
                    f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={from_lang}&tl=he&dt=t&q={encoded}",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                data = r.json()
                result = ""
                if data and data[0]:
                    for part in data[0]:
                        if part and part[0]:
                            result += part[0]
                result_parts = result.split("\n")
                for j in range(len(chunk)):
                    if j < len(result_parts) and result_parts[j]:
                        translated_lines[i+j] = result_parts[j]
            except Exception:
                pass
    
    result_lines = []
    for i, line in enumerate(lines):
        if line_map[i] is not None:
            result_lines.append(translated_lines[line_map[i]])
        else:
            result_lines.append(line)
    
    return JSONResponse({"translated": "\n".join(result_lines)})


# ═══ ANIME STREAM ═══
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)


@app.post("/api/translate")
async def translate_subtitles(request: dict):
    text = request.get("text", "")
    if not text:
        return JSONResponse({"translated": ""})
    
    # Try HuggingFace Helsinki-NLP (free, no key needed)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api-inference.huggingface.co/models/Helsinki-NLP/opus-mt-en-he",
                headers={"Content-Type": "application/json"},
                json={"inputs": text[:500]}
            )
            data = r.json()
            if isinstance(data, list) and data:
                translated = data[0].get("translation_text", "")
                if translated:
                    return JSONResponse({"translated": translated})
    except Exception:
        pass
    
    # Fallback: Google Translate
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=he&dt=t&q={httpx.URL(text[:500])}"
            r = await client.get(url)
            data = r.json()
            result = ""
            if data and data[0]:
                for part in data[0]:
                    if part and part[0]:
                        result += part[0]
            return JSONResponse({"translated": result or text})
    except Exception:
        return JSONResponse({"translated": text})
