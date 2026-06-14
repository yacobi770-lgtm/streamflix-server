from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncpg
import httpx
import jwt
import bcrypt
import os
from datetime import datetime, timedelta
from typing import Optional
import json

# ==================== CONFIG ====================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://srattv_user:srattv_pass@dpg-ctg5r8d2ng1s73f8pleg-a.frankfurt-postgres.render.com/srattv")
TMDB_API_KEY = "188a869e1edf45ff689684b7167be046"
REALDEBRID_API_KEY = "ZNPWJX7C226KSMAU335DAQYRPMGUDHVYSRYZVSWSD5WD6U7HP2RA"
OPENSUBTITLES_API_KEY = "uXMNABAThAoHpgZhdq6j4ncNifNNUmp5"
JWT_SECRET = "your_jwt_secret_key_change_this_in_production"
JWT_ALGORITHM = "HS256"

# ==================== FASTAPI APP ====================
app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== DATABASE ====================
async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ==================== MODELS ====================
class User(BaseModel):
    username: str
    email: str
    password: str
    accept_tos: bool

class LoginRequest(BaseModel):
    username: str
    password: str

class UpdateProfileRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None

# ==================== UTILITY FUNCTIONS ====================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_jwt(username: str, is_premium: bool = False) -> str:
    payload = {
        "username": username,
        "is_premium": is_premium,
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ==================== AUTH ENDPOINTS ====================
@app.post("/api/register")
async def register(user: User, db = Depends(get_db)):
    try:
        # Check if user exists
        existing = await db.fetchval("SELECT username FROM users WHERE username = $1", user.username)
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        
        # Hash password
        hashed_pw = hash_password(user.password)
        
        # Insert user
        await db.execute(
            "INSERT INTO users (username, email, password_hash, tier, created_at, accept_tos) VALUES ($1, $2, $3, $4, $5, $6)",
            user.username, user.email, hashed_pw, "free", datetime.utcnow(), user.accept_tos
        )
        
        # Create JWT
        token = create_jwt(user.username, is_premium=False)
        
        return {"message": "User registered", "token": token, "username": user.username}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/login")
async def login(req: LoginRequest, db = Depends(get_db)):
    try:
        user = await db.fetchrow("SELECT username, password_hash, tier FROM users WHERE username = $1", req.username)
        if not user or not verify_password(req.password, user['password_hash']):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        is_premium = user['tier'] == "premium"
        token = create_jwt(req.username, is_premium=is_premium)
        
        return {"message": "Login successful", "token": token, "username": req.username, "tier": user['tier']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logout")
async def logout():
    return {"message": "Logged out"}

@app.get("/api/profile")
async def get_profile(token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        user = await db.fetchrow("SELECT username, email, tier, created_at FROM users WHERE username = $1", payload['username'])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/profile")
async def update_profile(req: UpdateProfileRequest, token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        updates = []
        values = []
        
        if req.email:
            updates.append(f"email = ${len(values)+1}")
            values.append(req.email)
        if req.password:
            hashed_pw = hash_password(req.password)
            updates.append(f"password_hash = ${len(values)+1}")
            values.append(hashed_pw)
        
        if not updates:
            raise HTTPException(status_code=400, detail="No updates provided")
        
        values.append(payload['username'])
        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ${len(values)}"
        await db.execute(query, *values)
        
        return {"message": "Profile updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== TMDB ENDPOINTS ====================
@app.get("/api/tmdb/trending")
async def get_trending(page: int = 1):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/trending/all/week?api_key={TMDB_API_KEY}&language=he-IL&page={page}"
            response = await client.get(url)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tmdb/search")
async def search_tmdb(q: str, page: int = 1):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={q}&language=he-IL&page={page}"
            response = await client.get(url)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tmdb/details/{media_type}/{id}")
async def get_tmdb_details(media_type: str, id: int):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/{media_type}/{id}?api_key={TMDB_API_KEY}&language=he-IL&append_to_response=credits,videos"
            response = await client.get(url)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== REAL-DEBRID ENDPOINTS ====================
@app.get("/api/rd/unrestrict")
async def rd_unrestrict(link: str, token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        if not payload.get('is_premium'):
            raise HTTPException(status_code=403, detail="Premium only")
        
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {REALDEBRID_API_KEY}"}
            response = await client.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                                        data={"link": link}, 
                                        headers=headers)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== OPENSUBTITLES ENDPOINTS ====================
@app.get("/api/subtitles/search")
async def search_subtitles(imdb_id: str, language: str = "he"):
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Api-Key": OPENSUBTITLES_API_KEY}
            params = {"imdb_id": imdb_id, "languages": language}
            response = await client.get("https://api.opensubtitles.com/api/v1/subtitles",
                                       headers=headers,
                                       params=params)
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ADMIN ENDPOINTS ====================
@app.post("/api/admin/login")
async def admin_login(username: str, password: str, db = Depends(get_db)):
    ADMIN_USERNAME = "yacobi770"
    ADMIN_PASSWORD_HASH = hash_password("yacobi770")
    
    if username != ADMIN_USERNAME or not verify_password(password, ADMIN_PASSWORD_HASH):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_jwt(username, is_premium=True)
    return {"message": "Admin logged in", "token": token}

@app.get("/api/admin/users")
async def get_users(token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        if payload['username'] != "yacobi770":
            raise HTTPException(status_code=403, detail="Admin only")
        
        users = await db.fetch("SELECT username, email, tier, created_at FROM users")
        return {"users": [dict(u) for u in users]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/admin/user/{username}/tier")
async def update_user_tier(username: str, tier: str, token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        if payload['username'] != "yacobi770":
            raise HTTPException(status_code=403, detail="Admin only")
        
        if tier not in ["free", "premium"]:
            raise HTTPException(status_code=400, detail="Invalid tier")
        
        await db.execute("UPDATE users SET tier = $1 WHERE username = $2", tier, username)
        return {"message": f"User {username} tier updated to {tier}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== M3U8 PROXY ====================
@app.get("/api/m3u-proxy")
async def m3u_proxy(url: str):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            return StreamingResponse(iter([response.content]), media_type="application/vnd.apple.mpegurl")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== PRIVACY PAGE ====================
@app.get("/privacy")
async def privacy():
    html_content = """
    <!DOCTYPE html>
    <html lang="he" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>מדיניות פרטיות - SratTV</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                background: #060A12; 
                color: #F1F5FF; 
                font-family: 'Outfit', sans-serif; 
                padding: 20px;
                line-height: 1.6;
            }
            .container { max-width: 800px; margin: 0 auto; }
            h1 { margin: 20px 0; color: #4F8EFF; }
            h2 { margin: 15px 0 10px; color: #7DD3FC; }
            p { margin: 10px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>מדיניות פרטיות - SratTV</h1>
            
            <h2>1. מידע שאנו אוספים</h2>
            <p>SratTV אוספת את המידע הבא:</p>
            <p>• מידע חשבון (שם משתמש, דוא"ל, סיסמה מוצפנת)</p>
            <p>• נתוני שימוש (סרטים שנצפו, חיפושים, הערכות)</p>
            <p>• מידע טכני (IP address, סוג דיוויס, מערכת הפעלה)</p>

            <h2>2. כיצד אנו משתמשים בנתונים</h2>
            <p>אנו משתמשים בנתונים כדי:</p>
            <p>• לספק את השירות ולשמור על חשבונך</p>
            <p>• לשפר את הופעת האתר וחווית המשתמש</p>
            <p>• להציג תוכן מותאם אישית</p>
            <p>• לזהות ולמנוע הונאה וניסיונות הסתת סימן</p>

            <h2>3. אחסון נתונים</h2>
            <p>הנתונים שלך מאוחסנים בשרתים מאובטחים. אנו משתמשים בהצפנה ל-HTTPS כדי להגן על נתונים בשידור.</p>

            <h2>4. שיתוף נתונים</h2>
            <p>אנו לא משתפים את מידע פרטיך עם צד שלישי ללא הסכמתך.</p>

            <h2>5. זכויות המשתמש</h2>
            <p>יש לך זכות לבקש, לערוך או למחוק את הנתונים האישיים שלך. צור קשר דרך: support@srattv.com</p>

            <h2>6. שינויים למדיניות זו</h2>
            <p>אנו עשויים לעדכן את מדיניות זו מעת לעת. שינויים יכנסו לתוקף בעת הפרסום.</p>

            <p style="margin-top: 30px; font-size: 0.9em; color: #8899B8;">
                עודכן: ביוני 2026
            </p>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# ==================== HEALTH CHECK ====================
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# ==================== ROOT ====================
@app.get("/")
async def root():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return {"message": "SratTV API running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
