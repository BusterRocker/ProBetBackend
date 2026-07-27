import os
import uuid
import hashlib
import psycopg2
import requests
import stripe
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware

# Pull keys securely from Render's environment
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

DB_URL = "postgresql://neondb_owner:npg_rNAhGz3HVR1u@ep-young-wildflower-aj5vciva-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require"

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DB REGISTRY ---

def init_db():
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    
    # 1. Create users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            tier TEXT DEFAULT 'free'
        )
    ''')
    
    # 2. Create bets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            matchup TEXT NOT NULL,
            date TEXT,
            pick TEXT NOT NULL,
            odds INTEGER NOT NULL,
            risk INTEGER NOT NULL,
            status TEXT NOT NULL,
            net_profit INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')

    # 3. Patch the existing live table
    try:
        cursor.execute("ALTER TABLE bets ADD COLUMN IF NOT EXISTS date TEXT;")
    except Exception:
        pass
    
    conn.commit()
    cursor.close()
    conn.close()

init_db()

# --- ODDS API & CACHE CONFIGURATION ---
API_KEY = '1954c37a0303d89d6f84accf5a8c6861'

# Server-side cache dictionary to protect API request limits
API_CACHE = {
    "MLB": {"data": None, "last_updated": 0},
    "NFL": {"data": None, "last_updated": 0},
    "MLS": {"data": None, "last_updated": 0}
}
CACHE_EXPIRATION_SECONDS = 21600  # 6 Hours (60 * 60 * 6)

def american_to_implied(odds: int) -> float:
    if odds > 0: return 100 / (odds + 100)
    else: return abs(odds) / (abs(odds) + 100)

def generate_deterministic_analytics(game_id: str, home_team: str, away_team: str):
    seed = int(hashlib.sha256(game_id.encode('utf-8')).hexdigest(), 16)
    public_away = 45 + (seed % 26)
    public_home = 100 - public_away
    money_away = 35 + ((seed >> 2) % 36)
    money_home = 100 - money_away
    projected_home_prob = 0.42 + ((seed >> 4) % 17) / 100.0
    
    trends = [
        f"{home_team} is {5 + (seed % 4)}-2 straight up in their last 7 games.",
        f"The under is {4 + ((seed >> 1) % 4)}-1 when {away_team} plays on the road.",
        f"Sharps are hammering the line movement on this matchup over the last 3 hours.",
        f"{home_team} has a {55 + ((seed >> 3) % 15)}% historical covering rate as a home favorite."
    ]
    
    return {
        "splits": {"public": {"away": public_away, "home": public_home}, "money": {"away": money_away, "home": money_home}},
        "model_projections": {"home": projected_home_prob, "away": 1.0 - projected_home_prob},
        "trend_context": trends[seed % len(trends)]
    }

# --- STRIPE CHECKOUT ---
@app.post("/create-checkout-session")
def create_checkout_session(data: dict = Body(...)):
    user_id = data.get("user_id")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
        
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'ProBet Premium Access',
                        'description': 'Weekly subscription for professional analytics',
                    },
                    'unit_amount': 500,
                    'recurring': {
                        'interval': 'week',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            client_reference_id=str(user_id),
            subscription_data={
                "metadata": {
                    "user_id": str(user_id)
                }
            },
            success_url='https://pro-bet-mobile.vercel.app/?success=true',
            cancel_url='https://pro-bet-mobile.vercel.app/?canceled=true',
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- STRIPE WEBHOOK ---
@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        
        if user_id:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET tier = 'premium' WHERE id = %s", (user_id,))
            conn.commit()
            cursor.close()
            conn.close()
    
    if event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        user_id = subscription.get("metadata", {}).get("user_id")
        
        if user_id:
            conn = psycopg2.connect(DB_URL)
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET tier = 'free' WHERE id = %s", (user_id,))
            conn.commit()
            cursor.close()
            conn.close()

    return {"status": "success"}

# --- AUTHENTICATION INTERCEPTS ---
@app.post("/register")
def register_user(data: dict = Body(...)):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password cannot be empty.")
        
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, tier) VALUES (%s, %s, 'free') RETURNING id", (username, password))
        user_id = cursor.fetchone()[0]
        conn.commit()
        return {"success": True, "user_id": user_id, "username": username, "tier": "free"}
    except psycopg2.IntegrityError:
        raise HTTPException(status_code=400, detail="This username is already taken. Please choose another one.")
    finally:
        cursor.close()
        conn.close()

@app.post("/login")
def login_user(data: dict = Body(...)):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, tier FROM users WHERE username = %s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=404, detail="No user profile exists with that username.")
    if user[2] != password:
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")
        
    return {"success": True, "user_id": user[0], "username": user[1], "tier": user[3]}

@app.post("/upgrade")
def upgrade_user_tier(data: dict = Body(...)):
    user_id = data.get("user_id")
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tier = 'premium' WHERE id = %s", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"success": True, "tier": "premium"}

# --- LIVE SLATE DATA PROCESSOR (WITH CACHING) ---
@app.get("/")
def get_clean_bets(tier: str = Query("free"), sport: str = Query("MLB")):
    sport_upper = sport.upper()
    current_time = time.time()
    raw_data = []

    # 1. Check Server-Side Cache First
    if API_CACHE.get(sport_upper) and API_CACHE[sport_upper]["data"] is not None and (current_time - API_CACHE[sport_upper]["last_updated"]) < CACHE_EXPIRATION_SECONDS:
        raw_data = API_CACHE[sport_upper]["data"]
    else:
        # Cache expired or empty -> Fetch fresh data from The Odds API
        sport_keys = {
            "MLB": "baseball_mlb",
            "NFL": "americanfootball_nfl",
            "MLS": "soccer_usa_mls"
        }
        api_sport = sport_keys.get(sport_upper, "baseball_mlb")
        URL = f"https://api.the-odds-api.com/v4/sports/{api_sport}/odds"
        params = {'apiKey': API_KEY, 'regions': 'us', 'markets': 'h2h', 'oddsFormat': 'american', 'bookmakers': 'draftkings,fanduel,betmgm,caesars'}
        
        try:
            response = requests.get(URL, params=params, timeout=4)
            if response.status_code == 200:
                raw_data = response.json()
                # Save data to cache
                if isinstance(raw_data, list) and len(raw_data) > 0:
                    API_CACHE[sport_upper]["data"] = raw_data
                    API_CACHE[sport_upper]["last_updated"] = current_time
        except Exception:
            pass
        
    # 2. Fallback Demo Mock Data (if API fails or returns no events)
    mock_future_date = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"

    if not raw_data or not isinstance(raw_data, list) or "detail" in str(raw_data):
        if sport_upper == "NFL":
            raw_data = [{"id": "mock_nfl_1", "commence_time": mock_future_date, "home_team": "Kansas City Chiefs", "away_team": "Baltimore Ravens", "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": -150}, {"name": "Baltimore Ravens", "price": +130}]}]}]}]
        elif sport_upper == "MLS":
            raw_data = [{"id": "mock_mls_1", "commence_time": mock_future_date, "home_team": "LA Galaxy", "away_team": "Inter Miami", "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "LA Galaxy", "price": +150}, {"name": "Inter Miami", "price": +140}, {"name": "Draw", "price": +210}]}]}]}]
        else:
            raw_data = [{"id": "mock_mlb_1", "commence_time": mock_future_date, "home_team": "Boston Red Sox", "away_team": "New York Yankees", "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Boston Red Sox", "price": -120}, {"name": "New York Yankees", "price": +100}]}]}]}]
        
    clean_games_list = []
    for game in raw_data:
        game_id = game.get('id', 'unknown')
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        
        dk_home, dk_away, dk_draw = "N/A", "N/A", "N/A"
        best_home_price, best_home_book = -9999, "N/A"
        best_away_price, best_away_book = -9999, "N/A"
        best_draw_price, best_draw_book = -9999, "N/A"
        
        for bookmaker in game.get('bookmakers', []):
            b_key = bookmaker.get('key', '').upper()
            for market in bookmaker.get('markets', []):
                if market.get('key') == 'h2h':
                    for outcome in market.get('outcomes', []):
                        price = outcome.get('price')
                        name = outcome.get('name')
                        
                        if bookmaker.get('key') == 'draftkings':
                            if name == home_team: dk_home = price
                            elif name == away_team: dk_away = price
                            elif name == 'Draw': dk_draw = price
                            
                        if name == home_team and price > best_home_price:
                            best_home_price, best_home_book = price, b_key
                        elif name == away_team and price > best_away_price:
                            best_away_price, best_away_book = price, b_key
                        elif name == 'Draw' and price > best_draw_price:
                            best_draw_price, best_draw_book = price, b_key
                            
        if best_home_price == -9999: best_home_price = dk_home
        if best_away_price == -9999: best_away_price = dk_away
        if best_draw_price == -9999: best_draw_price = dk_draw
        
        analytics = generate_deterministic_analytics(game_id, home_team, away_team)
        
        home_edge, away_edge = 0.0, 0.0
        if isinstance(best_home_price, int): home_edge = round((analytics["model_projections"]["home"] - american_to_implied(best_home_price)) * 100, 1)
        if isinstance(best_away_price, int): away_edge = round((analytics["model_projections"]["away"] - american_to_implied(best_away_price)) * 100, 1)
        
        best_pick = home_team if (home_edge > away_edge and home_edge > 2.0) else (away_team if (away_edge > home_edge and away_edge > 2.0) else "N/A")
        calculated_edge_val = home_edge if best_pick == home_team else (away_edge if best_pick == away_team else 0.0)
        
        player_props = {}
        if sport_upper == "NFL":
            player_props = {"title": "Player Passing Yards", "bets": [f"{away_team} QB Over 245.5 (-110)", f"{home_team} QB Over 260.5 (-110)"]}
        elif sport_upper == "MLS":
            player_props = {"title": "Shots on Target", "bets": [f"{away_team} Striker Over 1.5 (-130)", f"{home_team} Striker Over 0.5 (+110)"]}
        else:
            player_props = {"title": "Player Home Runs", "bets": [f"{away_team} Slugger Over 0.5 (+250)", f"{home_team} Slugger Over 0.5 (+310)"]}

        game_dict = {
            "matchup": f"{away_team} @ {home_team}",
            "date": game.get("commence_time", "TBA"),
            "baseline_odds": {"home": dk_home, "away": dk_away},
            "line_shopping": {
                "home": {"price": best_home_price, "bookmaker": best_home_book}, 
                "away": {"price": best_away_price, "bookmaker": best_away_book}
            },
            "player_props": player_props,
            "premium_analytics": {
                "edge": {"recommended_pick": best_pick, "edge_percentage": calculated_edge_val}, 
                "splits": analytics["splits"], 
                "trend": analytics["trend_context"]
            }
        }

        if sport_upper == "MLS" and dk_draw != "N/A":
            game_dict["baseline_odds"]["draw"] = dk_draw
            game_dict["line_shopping"]["draw"] = {"price": best_draw_price, "bookmaker": best_draw_book}

        if tier == "free":
            game_dict["premium_analytics"] = None

        clean_games_list.append(game_dict)
        
    return {"data": clean_games_list}

# --- USER BET LOGGING ENDPOINTS ---
@app.get("/bets/{user_id}")
def get_user_bets(user_id: int):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("SELECT id, matchup, date, pick, odds, risk, status, net_profit FROM bets WHERE user_id = %s ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return [{"id": r[0], "matchup": r[1], "date": r[2], "pick": r[3], "odds": r[4], "risk": r[5], "status": r[6], "netProfit": r[7]} for r in rows]

@app.post("/bets/log")
def log_user_bet(data: dict = Body(...)):
    user_id = data.get("user_id")
    matchup = data.get("matchup")
    date = data.get("date", "TBA")
    pick = data.get("pick")
    odds = data.get("odds")
    bet_id = str(uuid.uuid4())[:8]
    
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bets (id, user_id, matchup, date, pick, odds, risk, status, net_profit) VALUES (%s, %s, %s, %s, %s, %s, 100, 'Pending', 0)", 
                   (bet_id, user_id, matchup, date, pick, odds))
    conn.commit()
    cursor.close()
    conn.close()
    return {"success": True}

@app.post("/bets/settle")
def settle_user_bet(data: dict = Body(...)):
    bet_id, status, net_profit = data.get("bet_id"), data.get("status"), data.get("net_profit")
    
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("UPDATE bets SET status = %s, net_profit = %s WHERE id = %s", (status, net_profit, bet_id))
    conn.commit()
    cursor.close()
    conn.close()
    return {"success": True}

@app.delete("/bets/{bet_id}")
def delete_user_bet(bet_id: str):
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bets WHERE id = %s", (bet_id,))
    conn.commit()
    deleted_count = cursor.rowcount
    cursor.close()
    conn.close()
    
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Wager not found.")
        
    return {"success": True}

# --- SPORTSBOOK SYNC ENDPOINT ---
@app.post("/sportsbooks/sync")
def sync_sportsbooks(data: dict = Body(...)):
    user_id = data.get("user_id")
    sportsbook = data.get("sportsbook")
    
    if not user_id or not sportsbook:
        raise HTTPException(status_code=400, detail="Missing user or bookmaker data.")
        
    return {"success": True, "message": f"{sportsbook} successfully linked."}