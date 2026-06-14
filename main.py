from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
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
    try:
        conn = await asyncpg.connect(DATABASE_URL)
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
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except:
        return False

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

# ==================== SETUP ENDPOINT ====================
@app.get("/api/setup")
async def setup_db(db = Depends(get_db)):
    """Create tables and admin user"""
    try:
        # Create users table
        await db.execute("""
            DROP TABLE IF EXISTS users CASCADE
        """)
        
        await db.execute("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accept_tos BOOLEAN DEFAULT FALSE
            )
        """)
        
        # Create admin user
        hashed_pw = hash_password("yacobi770")
        await db.execute(
            "INSERT INTO users (username, email, password_hash, created_at, accept_tos) VALUES ($1, $2, $3, $4, $5)",
            "yacobi770",
            "yacobi770@srattv.com",
            hashed_pw,
            datetime.utcnow(),
            True
        )
        
        return JSONResponse(status_code=200, content={
            "message": "Database setup complete - tables created and admin user added",
            "admin_created": True,
            "username": "yacobi770",
            "password": "yacobi770",
            "email": "yacobi770@srattv.com"
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Setup error: {str(e)}"})

# ==================== AUTH ENDPOINTS ====================
@app.post("/api/register")
async def register(user: User, db = Depends(get_db)):
    """Register new user"""
    try:
        # Validate input
        if not user.username or not user.email or not user.password:
            return JSONResponse(status_code=400, content={"detail": "Missing required fields"})
        
        if len(user.username) < 3:
            return JSONResponse(status_code=400, content={"detail": "Username must be at least 3 characters"})
        
        if len(user.password) < 3:
            return JSONResponse(status_code=400, content={"detail": "Password must be at least 3 characters"})
        
        if not user.accept_tos:
            return JSONResponse(status_code=400, content={"detail": "Must accept terms of service"})
        
        # Check if user exists
        existing = await db.fetchval("SELECT id FROM users WHERE username = $1 OR email = $2", user.username, user.email)
        if existing:
            return JSONResponse(status_code=409, content={"detail": "Username or email already exists"})
        
        # Hash password
        hashed_pw = hash_password(user.password)
        
        # Insert user
        try:
            await db.execute(
                "INSERT INTO users (username, email, password_hash, created_at, accept_tos) VALUES ($1, $2, $3, $4, $5)",
                user.username, user.email, hashed_pw, datetime.utcnow(), user.accept_tos
            )
        except Exception as e:
            return JSONResponse(status_code=400, content={"detail": f"Registration failed: {str(e)}"})
        
        # Create JWT
        is_premium = user.username == "yacobi770"
        token = create_jwt(user.username, is_premium=is_premium)
        
        return JSONResponse(status_code=200, content={
            "message": "User registered successfully",
            "token": token,
            "username": user.username,
            "email": user.email,
            "tier": "premium" if is_premium else "free"
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Server error: {str(e)}"})

@app.post("/api/login")
async def login(req: LoginRequest, db = Depends(get_db)):
    """Login user"""
    try:
        # Validate input
        if not req.username or not req.password:
            return JSONResponse(status_code=400, content={"detail": "Username and password required"})
        
        # Get user from database
        user = await db.fetchrow("SELECT id, username, password_hash FROM users WHERE username = $1", req.username)
        
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})
        
        # Verify password
        if not verify_password(req.password, user['password_hash']):
            return JSONResponse(status_code=401, content={"detail": "Invalid username or password"})
        
        # Create JWT
        is_premium = req.username == "yacobi770"
        token = create_jwt(req.username, is_premium=is_premium)
        
        return JSONResponse(status_code=200, content={
            "message": "Login successful",
            "token": token,
            "username": req.username,
            "tier": "premium" if is_premium else "free"
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": f"Server error: {str(e)}"})

@app.post("/api/logout")
async def logout():
    return JSONResponse(status_code=200, content={"message": "Logged out"})

@app.get("/api/profile")
async def get_profile(token: str = Query(...), db = Depends(get_db)):
    """Get user profile"""
    try:
        payload = decode_jwt(token)
        user = await db.fetchrow("SELECT username, email, created_at FROM users WHERE username = $1", payload['username'])
        
        if not user:
            return JSONResponse(status_code=404, content={"detail": "User not found"})
        
        is_premium = payload['username'] == "yacobi770"
        
        return JSONResponse(status_code=200, content={
            "username": user['username'],
            "email": user['email'],
            "tier": "premium" if is_premium else "free",
            "created_at": user['created_at'].isoformat() if user['created_at'] else None
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.patch("/api/profile")
async def update_profile(req: UpdateProfileRequest, token: str = Query(...), db = Depends(get_db)):
    """Update user profile"""
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
            return JSONResponse(status_code=400, content={"detail": "No updates provided"})
        
        values.append(payload['username'])
        query = f"UPDATE users SET {', '.join(updates)} WHERE username = ${len(values)}"
        await db.execute(query, *values)
        
        return JSONResponse(status_code=200, content={"message": "Profile updated"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==================== TMDB ENDPOINTS ====================
@app.get("/api/tmdb/trending")
async def get_trending(page: int = 1):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/trending/all/week?api_key={TMDB_API_KEY}&language=he-IL&page={page}"
            response = await client.get(url, timeout=10)
            return JSONResponse(status_code=200, content=response.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/api/tmdb/search")
async def search_tmdb(q: str, page: int = 1):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={q}&language=he-IL&page={page}"
            response = await client.get(url, timeout=10)
            return JSONResponse(status_code=200, content=response.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.get("/api/tmdb/details/{media_type}/{id}")
async def get_tmdb_details(media_type: str, id: int):
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.themoviedb.org/3/{media_type}/{id}?api_key={TMDB_API_KEY}&language=he-IL&append_to_response=credits,videos"
            response = await client.get(url, timeout=10)
            return JSONResponse(status_code=200, content=response.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==================== REAL-DEBRID ENDPOINTS ====================
@app.get("/api/rd/unrestrict")
async def rd_unrestrict(link: str, token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        if not payload.get('is_premium'):
            return JSONResponse(status_code=403, content={"detail": "Premium only"})
        
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {REALDEBRID_API_KEY}"}
            response = await client.post("https://api.real-debrid.com/rest/1.0/unrestrict/link", 
                                        data={"link": link}, 
                                        headers=headers)
            return JSONResponse(status_code=200, content=response.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

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
            return JSONResponse(status_code=200, content=response.json())
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==================== ADMIN ENDPOINTS ====================
@app.post("/api/admin/login")
async def admin_login(username: str, password: str, db = Depends(get_db)):
    ADMIN_USERNAME = "yacobi770"
    ADMIN_PASSWORD = "yacobi770"
    
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})
    
    token = create_jwt(username, is_premium=True)
    return JSONResponse(status_code=200, content={"message": "Admin logged in", "token": token})

@app.get("/api/admin/users")
async def get_users(token: str = Query(...), db = Depends(get_db)):
    try:
        payload = decode_jwt(token)
        if payload['username'] != "yacobi770":
            return JSONResponse(status_code=403, content={"detail": "Admin only"})
        
        users = await db.fetch("SELECT username, email, created_at FROM users")
        return JSONResponse(status_code=200, content={"users": [dict(u) for u in users]})
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

# ==================== M3U8 PROXY ====================
@app.get("/api/m3u-proxy")
async def m3u_proxy(url: str):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            return StreamingResponse(iter([response.content]), media_type="application/vnd.apple.mpegurl")
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})

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
    return JSONResponse(status_code=200, content={"status": "ok"})

# ==================== ROOT ====================
@app.get("/")
async def root():
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return JSONResponse(status_code=200, content={"message": "SratTV API running"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
