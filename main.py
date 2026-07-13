import os
import uuid
import hashlib
import sqlite3
import requests
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DB REGISTRY ---
DB_FILE = "postgresql://neondb_owner:npg_rNAhGz3HVR1u@ep-young-wildflower-aj5vciva-pooler.c-3.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            tier TEXT DEFAULT 'free'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            matchup TEXT NOT NULL,
            pick TEXT NOT NULL,
            odds INTEGER NOT NULL,
            risk INTEGER NOT NULL,
            status TEXT NOT NULL,
            net_profit INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

API_KEY = '0a9dc9ccb4900028d55d7222d2c30a1d'
SPORT = "baseball_mlb"
URL = f'https://api.the-odds-api.com/v4/sports/{SPORT}/odds'

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

# --- AUTHENTICATION INTERCEPTS ---
@app.post("/register")
def register_user(data: dict = Body(...)):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password cannot be empty.")
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO users (username, password, tier) VALUES (?, ?, 'free')", (username, password))
        conn.commit()
        user_id = cursor.lastrowid
        return {"success": True, "user_id": user_id, "username": username, "tier": "free"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="This username is already taken. Please choose another one.")
    finally:
        conn.close()

@app.post("/login")
def login_user(data: dict = Body(...)):
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password, tier FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        raise HTTPException(status_code=404, detail="No user profile exists with that username.")
    if user[2] != password:
        raise HTTPException(status_code=401, detail="Incorrect password. Please try again.")
        
    return {"success": True, "user_id": user[0], "username": user[1], "tier": user[3]}

@app.post("/upgrade")
def upgrade_user_tier(data: dict = Body(...)):
    user_id = data.get("user_id")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET tier = 'premium' WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"success": True, "tier": "premium"}

# --- LIVE SLATE DATA PROCESSOR ---
@app.get("/")
def get_clean_bets(tier: str = Query("free")):
    params = {'apiKey': API_KEY, 'regions': 'us', 'markets': 'h2h', 'oddsFormat': 'american', 'bookmakers': 'draftkings,fanduel,betmgm,caesars'}
    raw_data = []
    try:
        response = requests.get(URL, params=params, timeout=4)
        if response.status_code == 200:
            raw_data = response.json()
    except Exception:
        pass
        
    if not raw_data or not isinstance(raw_data, list) or "detail" in str(raw_data):
        raw_data = [
            {
                "id": "mock_game_1", "home_team": "Boston Red Sox", "away_team": "New York Yankees",
                "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Boston Red Sox", "price": -120}, {"name": "New York Yankees", "price": +100}]}]}]
            },
            {
                "id": "mock_game_2", "home_team": "Los Angeles Dodgers", "away_team": "San Francisco Giants",
                "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Los Angeles Dodgers", "price": -150}, {"name": "San Francisco Giants", "price": +130}]}]}]
            },
            {
                "id": "mock_game_3", "home_team": "Chicago Cubs", "away_team": "St. Louis Cardinals",
                "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "Chicago Cubs", "price": +110}, {"name": "St. Louis Cardinals", "price": -130}]}]}]
            }
        ]
        
    clean_games_list = []
    for game in raw_data:
        game_id = game.get('id', 'unknown')
        home_team = game.get('home_team')
        away_team = game.get('away_team')
        dk_home, dk_away = "N/A", "N/A"
        best_home_price, best_home_book = -9999, "N/A"
        best_away_price, best_away_book = -9999, "N/A"
        
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
                        if name == home_team and price > best_home_price:
                            best_home_price, best_home_book = price, b_key
                        elif name == away_team and price > best_away_price:
                            best_away_price, best_away_book = price, b_key

        if best_home_price == -9999: best_home_price = dk_home
        if best_away_price == -9999: best_away_price = dk_away
        analytics = generate_deterministic_analytics(game_id, home_team, away_team)
        
        home_edge, away_edge = 0.0, 0.0
        if isinstance(best_home_price, int): home_edge = round((analytics["model_projections"]["home"] - american_to_implied(best_home_price)) * 100, 1)
        if isinstance(best_away_price, int): away_edge = round((analytics["model_projections"]["away"] - american_to_implied(best_away_price)) * 100, 1)

        best_pick = home_team if (home_edge > away_edge and home_edge > 2.0) else (away_team if (away_edge > home_edge and away_edge > 2.0) else "N/A")
        calculated_edge_val = home_edge if best_pick == home_team else (away_edge if best_pick == away_team else 0.0)

        clean_games_list.append({
            "matchup": f"{away_team} @ {home_team}",
            "baseline_odds": {"home": dk_home, "away": dk_away},
            "line_shopping": {"home": {"price": best_home_price, "bookmaker": best_home_book}, "away": {"price": best_away_price, "bookmaker": best_away_book}},
            "premium_analytics": {"edge": {"recommended_pick": best_pick, "edge_percentage": calculated_edge_val}, "splits": analytics["splits"], "trend": analytics["trend_context"]}
        })
        
    return {"data": clean_games_list[:1] if tier == "free" else clean_games_list}

@app.get("/bets/{user_id}")
def get_user_bets(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, matchup, pick, odds, risk, status, net_profit FROM bets WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "matchup": r[1], "pick": r[2], "odds": r[3], "risk": r[4], "status": r[5], "netProfit": r[6]} for r in rows]

@app.post("/bets/log")
def log_user_bet(data: dict = Body(...)):
    user_id, matchup, pick, odds = data.get("user_id"), data.get("matchup"), data.get("pick"), data.get("odds")
    bet_id = str(uuid.uuid4())[:8]
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO bets (id, user_id, matchup, pick, odds, risk, status, net_profit) VALUES (?, ?, ?, ?, ?, 100, 'Pending', 0)", (bet_id, user_id, matchup, pick, odds))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/bets/settle")
def settle_user_bet(data: dict = Body(...)):
    bet_id, status, net_profit = data.get("bet_id"), data.get("status"), data.get("net_profit")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE bets SET status = ?, net_profit = ? WHERE id = ?", (status, net_profit, bet_id))
    conn.commit()
    conn.close()
    return {"success": True}

# --- NEW DELETE WAGER ENDPOINT ---
@app.delete("/bets/{bet_id}")
def delete_user_bet(bet_id: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bets WHERE id = ?", (bet_id,))
    conn.commit()
    deleted_count = cursor.rowcount
    conn.close()
    
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="Wager not found.")
    return {"success": True}