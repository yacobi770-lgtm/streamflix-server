from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cachetools import TTLCache
import uvicorn, httpx, json, re, os, jwt, bcrypt, secrets, asyncpg, smtplib, random, string
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def row_to_dict(r):
    """Convert asyncpg Record to JSON-serializable dict"""
    d = dict(r)
    for k, v in d.items():
        if hasattr(v, 'isoformat'):
            d[k] = v.isoformat()
        elif not isinstance(v, (str, int, float, bool, type(None), list, dict)):
            d[k] = str(v)
    return d


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ═══ CONFIG ═══
DATABASE_URL = os.getenv("DATABASE_URL", "")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "streamflix_admin_2024")
OPENSUBTITLES_KEY = "uXMNABAThAoHpgZhdq6j4ncNifNNUmp5"
RD_KEY = os.getenv("RD_KEY", "ZNPWJX7C226KSMAU335DAQYRPMGUDHVYSRYZVSWSD5WD6U7HP2RA")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SITE_URL = os.getenv("SITE_URL", "https://streamflix-server-production-e0cc.up.railway.app")

# ═══ TORRENTIO PROXY ═══
TORRENTIO_MIRRORS = [
    f"https://torrentio.strem.fun/realdebrid={RD_KEY}",
    f"https://torrentio.elfhosted.com/realdebrid={RD_KEY}",
]

stream_cache = TTLCache(maxsize=1024, ttl=43200)
subtitle_cache = TTLCache(maxsize=512, ttl=86400)
otp_store = {}  # email -> {code, expires}

# ═══ DATABASE ═══
db_pool = None

async def get_db():
    global db_pool
    if not db_pool and DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
    return db_pool

async def init_db():
    pool = await get_db()
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                avatar TEXT DEFAULT '🎬',
                is_premium BOOLEAN DEFAULT FALSE,
                premium_until TIMESTAMP,
                is_verified BOOLEAN DEFAULT FALSE,
                referral_code TEXT UNIQUE,
                referred_by INTEGER,
                watch_seconds INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_addons (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                addon_id TEXT NOT NULL,
                config JSONB DEFAULT '{}',
                installed_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, addon_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_history (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                item_type TEXT DEFAULT 'movie',
                title TEXT,
                poster TEXT,
                progress FLOAT DEFAULT 0,
                updated_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, item_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_watchlist (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                item_type TEXT DEFAULT 'movie',
                title TEXT,
                poster TEXT,
                added_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, item_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                item_type TEXT DEFAULT 'movie',
                title TEXT,
                rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                review TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, item_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                discount_pct INTEGER DEFAULT 50,
                uses_left INTEGER DEFAULT 100,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Insert default coupons
        await conn.execute("""
            INSERT INTO coupons (code, discount_pct, uses_left) VALUES ('LAUNCH50', 50, 1000)
            ON CONFLICT (code) DO NOTHING
        """)
        # IP + fingerprint tracking for trial abuse
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS trial_used (
                id SERIAL PRIMARY KEY,
                ip TEXT,
                fingerprint TEXT,
                email TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_trial_ip ON trial_used(ip)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_trial_fp ON trial_used(fingerprint)")

@app.on_event("startup")
async def startup():
    await init_db()

# ═══ JWT ═══
def create_token(user_id: int, username: str, is_premium: bool = False) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "is_premium": is_premium,
        "exp": datetime.utcnow() + timedelta(days=30)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None

security = HTTPBearer(auto_error=False)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        return None
    return verify_token(credentials.credentials)

# ═══ EMAIL ═══
def send_email(to: str, subject: str, html: str):
    if not SMTP_USER:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"SratTV <{SMTP_USER}>"
        msg["To"] = to
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to, msg.as_string())
    except Exception as e:
        print(f"Email error: {e}")

def gen_otp():
    return ''.join(random.choices(string.digits, k=6))

# ═══ STATIC ═══
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
async def root():
    # Try multiple locations
    for path in [
        os.path.join(BASE_DIR, "index.html"),
        "/app/index.html",
        "index.html",
        os.path.join(os.getcwd(), "index.html"),
    ]:
        if os.path.exists(path):
            return FileResponse(path)
    return HTMLResponse(f"<h1>index.html not found. BASE_DIR={BASE_DIR}, CWD={os.getcwd()}</h1>", status_code=404)

@app.get("/manifest.json")
async def manifest():
    return FileResponse(os.path.join(BASE_DIR, "manifest.json"))

@app.get("/sw.js")
async def sw():
    return FileResponse(os.path.join(BASE_DIR, "sw.js"))

# ═══ TRIAL ABUSE CHECK ═══
def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

@app.post("/api/check-trial")
async def check_trial(request: Request):
    """Check if IP or fingerprint already used free trial"""
    body = await request.json()
    fingerprint = body.get("fingerprint", "")
    ip = get_client_ip(request)
    
    pool = await get_db()
    if not pool:
        return JSONResponse({"abuse": False})
    
    async with pool.acquire() as conn:
        # Check IP
        ip_count = await conn.fetchval(
            "SELECT COUNT(*) FROM trial_used WHERE ip=$1 AND created_at > NOW() - INTERVAL '35 days'",
            ip
        )
        # Check fingerprint
        fp_count = await conn.fetchval(
            "SELECT COUNT(*) FROM trial_used WHERE fingerprint=$1 AND created_at > NOW() - INTERVAL '35 days'",
            fingerprint
        ) if fingerprint else 0
        
        abuse = (ip_count or 0) > 0 or (fp_count or 0) > 0
        return JSONResponse({
            "abuse": abuse,
            "ip_used": (ip_count or 0) > 0,
            "fp_used": (fp_count or 0) > 0
        })

@app.post("/api/register-trial")
async def register_trial(request: Request):
    """Register IP and fingerprint as having used trial"""
    body = await request.json()
    fingerprint = body.get("fingerprint", "")
    email = body.get("email", "")
    ip = get_client_ip(request)
    
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trial_used (ip, fingerprint, email) VALUES ($1, $2, $3)",
                ip, fingerprint, email
            )
    return JSONResponse({"success": True})

@app.get("/admin/trial-abuse")
async def admin_trial_abuse(secret: str = ""):
    """Admin: see all trial registrations"""
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ip, fingerprint, email, created_at FROM trial_used ORDER BY created_at DESC LIMIT 100"
            )
            # Find duplicate IPs
            dup_ips = await conn.fetch(
                "SELECT ip, COUNT(*) as cnt FROM trial_used GROUP BY ip HAVING COUNT(*) > 1 ORDER BY cnt DESC"
            )
            return JSONResponse({
                "trials": [row_to_dict(r) for r in rows],
                "duplicate_ips": [dict(r) for r in dup_ips]
            })
    return JSONResponse({"trials": [], "duplicate_ips": []})

# ═══ AUTH ═══
@app.post("/auth/register")
async def register(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    username = body.get("username", "").strip()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    referral = body.get("referral", "").strip().upper()

    if not username or not email or not password:
        raise HTTPException(status_code=400, detail="כל השדות נדרשים")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="סיסמה חייבת להיות לפחות 6 תווים")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    my_referral = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    pool = await get_db()
    if not pool:
        raise HTTPException(status_code=500, detail="שגיאת שרת")

    try:
        async with pool.acquire() as conn:
            referred_by = None
            if referral:
                ref_user = await conn.fetchrow("SELECT id FROM users WHERE referral_code=$1", referral)
                if ref_user:
                    referred_by = ref_user["id"]
                    # Give referrer 20 bonus days
                    await conn.execute("""
                        UPDATE users SET 
                          is_premium=TRUE,
                          premium_until=COALESCE(
                            GREATEST(premium_until, NOW()),
                            NOW()
                          ) + INTERVAL '20 days'
                        WHERE id=$1
                    """, ref_user["id"])

            user = await conn.fetchrow(
                "INSERT INTO users (username, email, password_hash, referral_code, referred_by) VALUES ($1,$2,$3,$4,$5) RETURNING id, username, email, is_premium",
                username, email, password_hash, my_referral, referred_by
            )

            # Send verification email only if SMTP is configured
            needs_verify = bool(SMTP_USER)
            if needs_verify:
                otp = gen_otp()
                otp_store[email] = {"code": otp, "expires": datetime.utcnow() + timedelta(minutes=15)}
                background_tasks.add_task(send_email, email, "אמת את האימייל שלך — SratTV", f"""
                    <div dir="rtl" style="font-family:sans-serif;max-width:400px;margin:auto;padding:2rem">
                    <h2 style="color:#4F8EFF">SratTV 🎬</h2>
                    <p>קוד האימות שלך:</p>
                    <div style="font-size:2rem;font-weight:900;letter-spacing:0.3em;text-align:center;background:#111;color:#4F8EFF;padding:1rem;border-radius:10px">{otp}</div>
                    <p style="color:#666;font-size:0.8rem">תקף ל-15 דקות</p>
                    </div>
                """)

            token = create_token(user["id"], user["username"], user["is_premium"])
            return JSONResponse({
                "token": token,
                "user": {"id": user["id"], "username": user["username"], "email": user["email"], "is_premium": user["is_premium"], "referral_code": my_referral},
                "needs_verification": needs_verify
            })
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=409, detail="שם משתמש או אימייל כבר קיים")

@app.post("/auth/verify-email")
async def verify_email(request: Request, user=Depends(get_current_user)):
    body = await request.json()
    code = body.get("code", "").strip()
    email = body.get("email", "").strip().lower()

    stored = otp_store.get(email)
    if not stored or stored["code"] != code:
        raise HTTPException(status_code=400, detail="קוד שגוי")
    if datetime.utcnow() > stored["expires"]:
        raise HTTPException(status_code=400, detail="קוד פג תוקף")

    del otp_store[email]
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET is_verified=TRUE WHERE email=$1", email)
    return JSONResponse({"success": True})

@app.post("/auth/forgot-password")
async def forgot_password(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            user = await conn.fetchrow("SELECT id FROM users WHERE email=$1", email)
            if user:
                otp = gen_otp()
                otp_store[f"reset_{email}"] = {"code": otp, "expires": datetime.utcnow() + timedelta(minutes=15)}
                background_tasks.add_task(send_email, email, "איפוס סיסמה — SratTV", f"""
                    <div dir="rtl" style="font-family:sans-serif;max-width:400px;margin:auto;padding:2rem">
                    <h2 style="color:#4F8EFF">SratTV 🎬</h2>
                    <p>קוד איפוס הסיסמה שלך:</p>
                    <div style="font-size:2rem;font-weight:900;letter-spacing:0.3em;text-align:center;background:#111;color:#F87171;padding:1rem;border-radius:10px">{otp}</div>
                    <p style="color:#666;font-size:0.8rem">תקף ל-15 דקות</p>
                    </div>
                """)
    return JSONResponse({"success": True})

@app.post("/auth/reset-password")
async def reset_password(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    code = body.get("code", "").strip()
    new_password = body.get("password", "")
    stored = otp_store.get(f"reset_{email}")
    if not stored or stored["code"] != code or datetime.utcnow() > stored["expires"]:
        raise HTTPException(status_code=400, detail="קוד שגוי או פג תוקף")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="סיסמה קצרה מדי")
    password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET password_hash=$1 WHERE email=$2", password_hash, email)
    del otp_store[f"reset_{email}"]
    return JSONResponse({"success": True})

@app.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    if not email or not password:
        raise HTTPException(status_code=400, detail="כל השדות נדרשים")
    pool = await get_db()
    if not pool:
        raise HTTPException(status_code=500, detail="שגיאת שרת")
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE email=$1", email)
        if not user:
            raise HTTPException(status_code=401, detail="אימייל או סיסמה שגויים")
        if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            raise HTTPException(status_code=401, detail="אימייל או סיסמה שגויים")
        # Check premium expiry
        is_premium = user["is_premium"]
        if is_premium and user["premium_until"] and user["premium_until"] < datetime.utcnow():
            is_premium = False
            await conn.execute("UPDATE users SET is_premium=FALSE WHERE id=$1", user["id"])
        token = create_token(user["id"], user["username"], is_premium)
        return JSONResponse({
            "token": token,
            "user": {"id": user["id"], "username": user["username"], "email": user["email"], "is_premium": is_premium, "avatar": user["avatar"], "referral_code": user["referral_code"]}
        })

@app.get("/auth/me")
async def get_me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="לא מחובר")
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            u = await conn.fetchrow("SELECT id,username,email,is_premium,avatar,referral_code,watch_seconds,created_at FROM users WHERE id=$1", user["user_id"])
            if u:
                return JSONResponse(dict(u))
    raise HTTPException(status_code=404)

# ═══ ADMIN ═══
@app.post("/admin/premium")
async def admin_set_premium(request: Request):
    body = await request.json()
    if body.get("secret") != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="אין הרשאה")
    email = body.get("email", "").strip().lower()
    months = body.get("months", 1)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            until = datetime.utcnow() + timedelta(days=30*months)
            result = await conn.fetchrow(
                "UPDATE users SET is_premium=TRUE, premium_until=$1 WHERE email=$2 RETURNING id,username,email",
                until, email
            )
            if result:
                return JSONResponse({"success": True, "user": dict(result), "until": str(until)})
    raise HTTPException(status_code=404, detail="משתמש לא נמצא")

@app.get("/admin/stats")
async def admin_stats(secret: str = ""):
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM users")
            premium = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_premium=TRUE")
            today = await conn.fetchval("SELECT COUNT(*) FROM users WHERE created_at > NOW() - INTERVAL '1 day'")
            return JSONResponse({"total_users": total, "premium_users": premium, "new_today": today})
    return JSONResponse({"total_users": 0, "premium_users": 0, "new_today": 0})

@app.get("/admin/users")
async def admin_users(secret: str = "", page: int = 1):
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            users = await conn.fetch(
                "SELECT id,username,email,is_premium,premium_until,created_at FROM users ORDER BY created_at DESC LIMIT 50 OFFSET $1",
                (page-1)*50
            )
            return JSONResponse([dict(u) for u in users])
    return JSONResponse([])


@app.post("/admin/reset-users")
async def admin_reset_users(request: Request):
    body = await request.json()
    if body.get("secret") != ADMIN_SECRET:
        raise HTTPException(status_code=403)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM user_addons")
            await conn.execute("DELETE FROM user_history")
            await conn.execute("DELETE FROM user_watchlist")
            await conn.execute("DELETE FROM ratings")
            await conn.execute("DELETE FROM trial_used")
            await conn.execute("DELETE FROM users")
            return JSONResponse({"success": True, "message": "כל המשתמשים נמחקו"})
    raise HTTPException(status_code=500)

@app.post("/admin/create-user")
async def admin_create_user(request: Request):
    body = await request.json()
    if body.get("secret") != ADMIN_SECRET:
        raise HTTPException(status_code=403)
    username = body.get("username", "").strip()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    is_premium = body.get("is_premium", False)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            referral_code = secrets.token_hex(4).upper()
            premium_until = datetime.utcnow() + timedelta(days=365) if is_premium else None
            user = await conn.fetchrow(
                """INSERT INTO users (username, email, password_hash, referral_code, is_premium, premium_until)
                   VALUES (,,,,,) RETURNING id,username,email,is_premium""",
                username, email, password_hash, referral_code, is_premium, premium_until
            )
            return JSONResponse({"success": True, "user": dict(user)})
    raise HTTPException(status_code=500)

# ═══ COUPON ═══
@app.post("/api/coupon")
async def apply_coupon(request: Request, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    code = body.get("code", "").strip().upper()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            coupon = await conn.fetchrow("SELECT * FROM coupons WHERE code=$1 AND uses_left > 0", code)
            if not coupon:
                raise HTTPException(status_code=404, detail="קופון לא תקין")
            if coupon["expires_at"] and coupon["expires_at"] < datetime.utcnow():
                raise HTTPException(status_code=400, detail="קופון פג תוקף")
            await conn.execute("UPDATE coupons SET uses_left=uses_left-1 WHERE code=$1", code)
            return JSONResponse({"discount": coupon["discount_pct"], "code": code})
    raise HTTPException(status_code=500)

# ═══ RATINGS ═══
@app.post("/api/ratings")
async def add_rating(request: Request, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ratings (user_id,item_id,item_type,title,rating,review) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_id,item_id) DO UPDATE SET rating=$5,review=$6",
                user["user_id"], str(body.get("id")), body.get("type","movie"), body.get("title",""), body.get("rating",5), body.get("review","")
            )
    return JSONResponse({"success": True})

@app.get("/api/ratings/{item_id}")
async def get_ratings(item_id: str):
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT r.*,u.username,u.avatar FROM ratings r JOIN users u ON r.user_id=u.id WHERE r.item_id=$1 ORDER BY r.created_at DESC LIMIT 20", item_id)
            avg = await conn.fetchval("SELECT AVG(rating) FROM ratings WHERE item_id=$1", item_id)
            return JSONResponse({"ratings": [row_to_dict(r) for r in rows], "average": float(avg) if avg else None})
    return JSONResponse({"ratings": [], "average": None})

# ═══ HISTORY ═══
@app.get("/api/history")
async def get_history(user=Depends(get_current_user)):
    if not user:
        return JSONResponse([])
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM user_history WHERE user_id=$1 ORDER BY updated_at DESC LIMIT 50", user["user_id"])
            return JSONResponse([row_to_dict(r) for r in rows])
    return JSONResponse([])

@app.post("/api/history")
async def add_history(request: Request, user=Depends(get_current_user)):
    if not user:
        return JSONResponse({"error": "not authenticated"})
    body = await request.json()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_history (user_id,item_id,item_type,title,poster,progress) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (user_id,item_id) DO UPDATE SET progress=$6,updated_at=NOW()",
                user["user_id"], str(body.get("id","")), body.get("type","movie"), body.get("title",""), body.get("poster",""), body.get("progress",0)
            )
    return JSONResponse({"success": True})

# ═══ WATCHLIST ═══
@app.get("/api/watchlist")
async def get_watchlist(user=Depends(get_current_user)):
    if not user:
        return JSONResponse([])
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM user_watchlist WHERE user_id=$1 ORDER BY added_at DESC", user["user_id"])
            return JSONResponse([row_to_dict(r) for r in rows])
    return JSONResponse([])

@app.post("/api/watchlist")
async def add_watchlist(request: Request, user=Depends(get_current_user)):
    if not user:
        return JSONResponse({"error": "not authenticated"})
    body = await request.json()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_watchlist (user_id,item_id,item_type,title,poster) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
                user["user_id"], str(body.get("id","")), body.get("type","movie"), body.get("title",""), body.get("poster","")
            )
    return JSONResponse({"success": True})

@app.delete("/api/watchlist/{item_id}")
async def remove_watchlist(item_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM user_watchlist WHERE user_id=$1 AND item_id=$2", user["user_id"], item_id)
    return JSONResponse({"success": True})

# ═══ ADDONS ═══
@app.get("/api/addons")
async def get_addons(user=Depends(get_current_user)):
    if not user:
        return JSONResponse([])
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM user_addons WHERE user_id=$1", user["user_id"])
            result = []
            for r in rows:
                row_dict = dict(r)
                # Convert non-serializable types
                for k, v in row_dict.items():
                    if hasattr(v, 'isoformat'):
                        row_dict[k] = v.isoformat()
                    elif not isinstance(v, (str, int, float, bool, type(None), list, dict)):
                        row_dict[k] = str(v)
                result.append(row_dict)
            return JSONResponse(result)
    return JSONResponse([])

@app.post("/api/addons/{addon_id}")
async def install_addon(addon_id: str, request: Request, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    body = await request.json()
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_addons (user_id,addon_id,config) VALUES ($1,$2,$3) ON CONFLICT (user_id,addon_id) DO UPDATE SET config=$3",
                user["user_id"], addon_id, json.dumps(body.get("config",{}))
            )
    return JSONResponse({"success": True})

@app.delete("/api/addons/{addon_id}")
async def remove_addon(addon_id: str, user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401)
    pool = await get_db()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM user_addons WHERE user_id=$1 AND addon_id=$2", user["user_id"], addon_id)
    return JSONResponse({"success": True})

# ═══ SUBTITLES ═══
@app.get("/api/subtitles/{imdb_id}")
async def get_subtitles(imdb_id: str, title: str = ""):
    cache_key = f"sub_{imdb_id}_{title}"
    if cache_key in subtitle_cache:
        return JSONResponse(subtitle_cache[cache_key])
    result = {"url": None, "source": None}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            clean_id = imdb_id.replace("tt","")
            r = await client.get(f"https://api.opensubtitles.com/api/v1/subtitles?imdb_id={clean_id}&languages=he&order_by=download_count", headers={"Api-Key": OPENSUBTITLES_KEY})
            items = r.json().get("data",[])
            if items:
                fid = items[0].get("attributes",{}).get("files",[{}])[0].get("file_id")
                if fid:
                    r2 = await client.post("https://api.opensubtitles.com/api/v1/download", headers={"Api-Key": OPENSUBTITLES_KEY, "Content-Type": "application/json"}, json={"file_id": fid})
                    link = r2.json().get("link")
                    if link: result = {"url": link, "source": "OpenSubtitles"}
        except: pass
        if not result["url"]:
            try:
                params = {"api_key":"free","languages":"HE"}
                if imdb_id: params["imdb_id"] = imdb_id
                elif title: params["film_name"] = title
                r = await client.get("https://api.subdl.com/api/v1/subtitles", params=params)
                subs = r.json().get("subtitles",[])
                if subs: result = {"url": f"https://dl.subdl.com{subs[0]['url']}", "source": "SubDL"}
            except: pass

        # SubSource
        if not result["url"] and imdb_id:
            try:
                r = await client.get(f"https://api.subsource.net/api/searchMovie", params={"query": title or imdb_id, "langs": "Hebrew"})
                data = r.json()
                if data.get("found") and data.get("movies"):
                    movie_id = data["movies"][0].get("id")
                    if movie_id:
                        r2 = await client.get(f"https://api.subsource.net/api/getMovie/{movie_id}")
                        subs = r2.json().get("subs", [])
                        heb = [s for s in subs if s.get("lang") == "Hebrew"]
                        if heb:
                            dl_id = heb[0].get("subId")
                            result = {"url": f"https://api.subsource.net/api/downloadSub/{dl_id}", "source": "SubSource"}
            except: pass

        # Podnapisi
        if not result["url"] and title:
            try:
                search_title = (title or "").replace(" ", "+")
                r = await client.get(f"https://www.podnapisi.net/subtitles/search/old?keywords={search_title}&lang=he&format=json")
                data = r.json()
                subs = data.get("subtitles", [])
                if subs:
                    sub_id = subs[0].get("id")
                    result = {"url": f"https://www.podnapisi.net/subtitles/{sub_id}/download", "source": "Podnapisi"}
            except: pass
        if not result["url"] and imdb_id:
            try:
                cid = imdb_id.replace("tt","")
                r = await client.get(f"https://wizdom.xyz/api/files?action=index&imdb={cid}&json=1")
                data = r.json()
                if isinstance(data,list) and data:
                    best = sorted(data, key=lambda x: x.get("downloads",0), reverse=True)
                    if best: result = {"url": f"https://wizdom.xyz/api/files?action=download&type=sub&id={best[0].get('id')}", "source": "Wizdom"}
            except: pass
    if result["url"]: subtitle_cache[cache_key] = result
    return JSONResponse(result)

@app.get("/api/subtitle-content")
async def get_subtitle_content(url: str):
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            content = r.content
            if url.endswith(".zip") or "zip" in r.headers.get("content-type",""):
                import io, zipfile
                try:
                    z = zipfile.ZipFile(io.BytesIO(content))
                    for name in z.namelist():
                        if name.endswith((".srt",".vtt",".ass",".ssa")):
                            content = z.read(name); break
                except: pass
            for enc in ["utf-8","windows-1255","iso-8859-8","latin-1"]:
                try: return JSONResponse({"content": content.decode(enc)})
                except: pass
            return JSONResponse({"content": content.decode("utf-8", errors="replace")})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/translate-srt")
async def translate_srt(request: Request):
    import urllib.parse
    body = await request.json()
    srt_text = body.get("srt","")
    from_lang = body.get("from_lang","auto")
    if not srt_text: return JSONResponse({"translated":""})
    lines = srt_text.split("\n")
    text_lines, line_map = [], []
    for line in lines:
        s = line.strip()
        if re.match(r"^\d+$",s) or "-->" in s or not s:
            line_map.append(None)
        else:
            line_map.append(len(text_lines)); text_lines.append(s)
    if not text_lines: return JSONResponse({"translated": srt_text})
    translated = list(text_lines)
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(text_lines), 80):
            chunk = text_lines[i:i+80]
            try:
                enc = urllib.parse.quote("\n".join(chunk))
                r = await client.get(f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={from_lang}&tl=he&dt=t&q={enc}", headers={"User-Agent":"Mozilla/5.0"})
                result = "".join(p[0] for p in r.json()[0] if p and p[0])
                parts = result.split("\n")
                for j in range(len(chunk)):
                    if j < len(parts) and parts[j]: translated[i+j] = parts[j]
            except: pass
    result_lines = [translated[line_map[i]] if line_map[i] is not None else line for i,line in enumerate(lines)]
    return JSONResponse({"translated": "\n".join(result_lines)})

# ═══ STREAMS ═══
@app.get("/api/streams/{media_type}/{media_id}")
async def get_streams(media_type: str, media_id: str, s: int = 1, e: int = 1):
    cache_key = f"{media_type}_{media_id}_{s}_{e}"
    if cache_key in stream_cache:
        return JSONResponse(stream_cache[cache_key])
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for mirror in TORRENTIO_MIRRORS:
            try:
                path = f"/stream/series/{media_id}:{s}:{e}.json" if media_type == "tv" else f"/stream/movie/{media_id}.json"
                r = await client.get(mirror + path, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = r.json()
                    if data.get("streams"):
                        stream_cache[cache_key] = data
                        return JSONResponse(data)
            except:
                continue
    return JSONResponse({"streams": []})


# ═══ M3U8 PROXY ═══
@app.get("/api/m3u-proxy")
async def m3u_proxy(url: str, request: Request):
    """Proxy for M3U8/IPTV playlists to bypass CORS and HTTP restrictions"""
    print(f"M3U Proxy request for: {url}")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=False) as client:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate"
            }
            r = await client.get(url, headers=headers)
            print(f"M3U Proxy response: {r.status_code}, size: {len(r.content)}")
            return Response(
                content=r.content,
                media_type="text/plain",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "text/plain; charset=utf-8",
                    "X-Content-Type-Options": "nosniff"
                }
            )
    except Exception as e:
        print(f"M3U Proxy error: {e}")
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
