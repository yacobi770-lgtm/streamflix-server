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

# ==================== UTILS ====================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(username: str) -> str:
    payload = {
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("username")
    except:
        return None

# ==================== API ENDPOINTS ====================

@app.post("/api/setup")
async def setup(conn: asyncpg.Connection = Depends(get_db)):
    """Initialize database with users table"""
    try:
        # Create users table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accept_tos BOOLEAN DEFAULT FALSE
            )
        """)
        
        # Create admin user
        admin_user = "yacobi770"
        admin_email = "admin@srattv.com"
        admin_pass = "yacobi770"
        
        hashed = hash_password(admin_pass)
        
        try:
            await conn.execute(
                """INSERT INTO users (username, email, password_hash, accept_tos) 
                   VALUES ($1, $2, $3, $4)""",
                admin_user, admin_email, hashed, True
            )
        except asyncpg.UniqueViolationError:
            pass  # User already exists
        
        return JSONResponse({"status": "setup_complete", "admin": admin_user})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/api/register")
async def register(user: User, conn: asyncpg.Connection = Depends(get_db)):
    """Register new user"""
    try:
        hashed_password = hash_password(user.password)
        
        await conn.execute(
            """INSERT INTO users (username, email, password_hash, accept_tos) 
               VALUES ($1, $2, $3, $4)""",
            user.username, user.email, hashed_password, user.accept_tos
        )
        
        token = create_token(user.username)
        return JSONResponse({
            "token": token,
            "username": user.username,
            "email": user.email
        })
    except asyncpg.UniqueViolationError as e:
        if "username" in str(e):
            return JSONResponse({"detail": "שם משתמש כבר קיים"}, status_code=400)
        else:
            return JSONResponse({"detail": "אימייל כבר קיים"}, status_code=400)
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

@app.post("/api/login")
async def login(req: LoginRequest, conn: asyncpg.Connection = Depends(get_db)):
    """Login user"""
    try:
        user_record = await conn.fetchrow(
            "SELECT * FROM users WHERE username = $1",
            req.username
        )
        
        if not user_record or not verify_password(req.password, user_record['password_hash']):
            return JSONResponse({"detail": "שם משתמש או סיסמה לא נכונים"}, status_code=401)
        
        token = create_token(req.username)
        
        return JSONResponse({
            "token": token,
            "username": user_record['username'],
            "email": user_record['email'],
            "is_premium": req.username == "yacobi770"
        })
    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)

# ==================== TMDB API ====================
@app.get("/api/popular")
async def get_popular(page: int = Query(1)):
    """Get popular movies from TMDB"""
    try:
        url = f"https://api.themoviedb.org/3/movie/popular?api_key={TMDB_API_KEY}&page={page}&language=he-IL"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            data = resp.json()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/search")
async def search_movies(query: str = Query(...)):
    """Search movies on TMDB"""
    try:
        url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}&language=he-IL"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            data = resp.json()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ==================== PRIVACY POLICY ====================
@app.get("/privacy")
async def privacy_policy():
    html_content = """
    <!DOCTYPE html>
    <html lang="he" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>מדיניות הפרטיות - SratTV</title>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                background: #060A12;
                color: #F1F5FF;
                font-family: 'Outfit', Arial, sans-serif;
                line-height: 1.8;
                padding: 20px;
            }
            .container {
                max-width: 900px;
                margin: 0 auto;
                background: linear-gradient(135deg, #0F1928 0%, #0B1120 100%);
                padding: 40px;
                border-radius: 16px;
                border: 1px solid rgba(79, 142, 255, 0.2);
            }
            h1 { color: #4F8EFF; margin-bottom: 30px; text-align: center; font-size: 32px; }
            h2 { color: #7DD3FC; margin-top: 25px; margin-bottom: 12px; font-size: 18px; }
            p { color: rgba(241, 245, 255, 0.8); margin-bottom: 10px; font-size: 14px; }
            strong { color: #FBBF24; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>מדיניות הפרטיות - SratTV</h1>
            
            <h2>1. מידע שאנו אוספים</h2>
            <p><strong>• מידע חשבון:</strong> שם משתמש, כתובת דוא"ל, סיסמה מוצפנת</p>
            <p><strong>• נתוני שימוש:</strong> סרטים וסדרות שנצפו, חיפושים, דירוגים וביקורות</p>
            <p><strong>• מידע טכני:</strong> כתובת IP, סוג התקן, מערכת הפעלה, סוג דפדפן</p>
            <p><strong>• מידע מיקום:</strong> מדינה כללית (לא יחידה)</p>

            <h2>2. כיצד אנו משתמשים בנתונים</h2>
            <p><strong>• ספקת השירות:</strong> לאפשר צפייה בסרטים וסדרות</p>
            <p><strong>• שיפור השירות:</strong> ניתוח שימוש ושיפור חווית המשתמש</p>
            <p><strong>• אבטחה:</strong> מניעת הונאה וזיהוי כפל חשבונות</p>
            <p><strong>• תוכן מותאם:</strong> המלצות בהתאם ללצפייתך</p>

            <h2>3. אחסון וביטחון נתונים</h2>
            <p>הנתונים שלך מאוחסנים בשרתים מאובטחים. אנו משתמשים בהצפנה SSL/HTTPS לכל התקשורת. סיסמאות מאוחסנות בצורה מוצפנת ולא ניתן לשחזרן.</p>

            <h2>4. שיתוף נתונים עם צדדים שלישיים</h2>
            <p>אנו לא משתפים את מידע פרטיך עם צדדים שלישיים ללא הסכמתך המפורשת.</p>

            <h2>5. זכויות המשתמש</h2>
            <p><strong>• זכות גישה:</strong> אתה יכול לבקש להציג את כל הנתונים שלך</p>
            <p><strong>• זכות תיקון:</strong> אתה יכול לבקש לתקן מידע שגוי</p>
            <p><strong>• זכות מחיקה:</strong> אתה יכול לבקש מחיקת חשבונך וכל הנתונים</p>
            <p>לבקשות כלשהן: <strong>support@srattv.com</strong></p>

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

# ==================== SERVE INDEX.HTML AT ROOT ====================
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve index.html with login page"""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # Fallback - serve minimal login page
        return """
        <!DOCTYPE html>
        <html>
        <head><title>SratTV</title></head>
        <body><p>Loading...</p></body>
        </html>
        """

@app.get("/app.html", response_class=HTMLResponse)
async def serve_app():
    """Serve main app"""
    try:
        with open("app.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return JSONResponse({"error": "app.html not found"}, status_code=404)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
