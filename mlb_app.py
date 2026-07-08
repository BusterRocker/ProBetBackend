import requests
import pandas as pd
from pybaseball import playerid_lookup, statcast_batter

# ==========================================
# PART 1: PULL LIVE ODDS (The Odds API)
# ==========================================
API_KEY = '0a9dc9ccb4900028d55d7222d2c30a1d' # Make sure to paste your real key here!
SPORT = 'baseball_mlb'
REGIONS = 'us'
MARKETS = 'h2h' # Changed to h2h (Moneyline) to make the bulk endpoint happy
ODDS_FORMAT = 'american'

def get_mlb_odds():
    print("Fetching live MLB odds...")
    # Updated to the clean bulk odds endpoint URL
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/odds'
    params = {
        'apiKey': API_KEY,
        'regions': REGIONS,
        'markets': MARKETS,
        'oddsFormat': ODDS_FORMAT
    }
    
    response = requests.get(url, params=params)
    if response.status_code != 200:
        print(f"Failed to get odds: {response.text}")
        return []
    
    return response.json()

# ==========================================
# PART 2: CALCULATE HIT RATE (pybaseball)
# ==========================================
def calculate_hit_rate(first_name, last_name, start_date, end_date):
    print(f"Analyzing historical data for {first_name} {last_name}...")
    
    # Look up player ID
    player = playerid_lookup(last_name, first_name)
    if player.empty:
        print("Player not found.")
        return
    
    mlbam_id = player['key_mlbam'].values[0]
    
    # Pull pitch data
    stats = statcast_batter(start_dt=start_date, end_dt=end_date, player_id=mlbam_id)
    
    if stats.empty:
        print("No data found for this date range.")
        return

    # Check for hits
    hit_events = ['single', 'double', 'triple', 'home_run']
    games_played = stats['game_date'].nunique()
    games_with_hit = stats[stats['events'].isin(hit_events)]['game_date'].nunique()
    
    hit_rate = (games_with_hit / games_played) * 100 if games_played > 0 else 0
    
    print(f"\n--- {first_name.upper()} {last_name.upper()} STATS ---")
    print(f"Games Played: {games_played}")
    print(f"Games with 1+ Hits: {games_with_hit}")
    print(f"Historical Hit Rate: {hit_rate:.1f}%\n")

# ==========================================
# PART 3: RUN THE APP
# ==========================================
if __name__ == "__main__":
    # Using a solid historical month from 2025 to confirm the math works perfectly
    calculate_hit_rate('aaron', 'judge', '2025-05-01', '2025-06-01')
    
    # Fetch and display the live team odds
    odds_data = get_mlb_odds()
    print("--- LIVE ODDS SAMPLE (First 2 Games) ---")
    print(odds_data[:2])