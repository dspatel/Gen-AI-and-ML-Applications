import yfinance as yf
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import sqlite3
import pandas as pd
import datetime
import os
import json
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.db')
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), 'data', 'world_state_checkpoint.txt')

NEWS_API_KEY = "19164633a36e4acda78e65ea0cda5fc9"
FRED_API_KEY = "6edacb8953cb602d0e88ba8693891f9d"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS world_state_log (
            date TEXT PRIMARY KEY,
            sentiment_score REAL,
            positive_prob REAL,
            negative_prob REAL,
            headlines JSON
        )
    ''')
    try:
        cur.execute('ALTER TABLE world_state_log ADD COLUMN fred_data JSON')
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute('ALTER TABLE world_state_log ADD COLUMN options_data JSON')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def get_missing_intervals():
    """
    Reads the checkpoint file to find the EXACT UTC millisecond we last queried.
    Generates 24-hour non-overlapping intervals up to 'now'.
    """
    now_utc = datetime.datetime.utcnow()
    
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH, 'r') as f:
            last_dt_str = f.read().strip()
            try:
                last_dt = datetime.datetime.fromisoformat(last_dt_str.replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                last_dt = now_utc - datetime.timedelta(days=1)
    else:
        # User requested backfill. We go back 14 days to perfectly clean and patch the dataset.
        print("No checkpoint found. Initializing deep 14-day backfill to clean dataset...")
        last_dt = now_utc - datetime.timedelta(days=14)
        
    intervals = []
    current_start = last_dt
    
    while current_start < now_utc:
        # Chunk tightly into 24 hour windows to assign to distinct trading days
        current_end = current_start + datetime.timedelta(hours=24)
        if current_end > now_utc:
            current_end = now_utc
            
        # Target date for the database is the date of the END of the interval
        target_date = current_end.date()
        
        intervals.append({
            'start_time': current_start,
            'end_time': current_end,
            'target_date': target_date
        })
        current_start = current_end
        
    return intervals, now_utc

def update_checkpoint(now_utc):
    with open(CHECKPOINT_PATH, 'w') as f:
        f.write(now_utc.strftime('%Y-%m-%dT%H:%M:%SZ'))

def fetch_newsapi_headlines(start_time, end_time):
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"Fetching Global Headlines from NewsAPI ({start_str} to {end_str})...")
    headlines = []
    
    try:
        # NewsAPI requires queries for precise historical bounds
        query = "(business OR economy) AND (US OR global)"
        url_biz = f"https://newsapi.org/v2/everything?q={query}&from={start_str}&to={end_str}&language=en&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
        
        response = requests.get(url_biz, timeout=10)
        if response.status_code == 200:
            for article in response.json().get('articles', [])[:20]:
                if article.get('title') and article['title'] != "[Removed]":
                    headlines.append({
                        "time": article.get("publishedAt", end_str),
                        "headline": article['title'],
                        "source": "NewsAPI-Business"
                    })
                    
        # Global geopolitical/macro
        url_global = f"https://newsapi.org/v2/everything?q=geopolitics OR supply chain OR federal reserve OR inflation AND NOT sports&from={start_str}&to={end_str}&language=en&sortBy=publishedAt&apiKey={NEWS_API_KEY}"
        response_global = requests.get(url_global, timeout=10)
        if response_global.status_code == 200:
            for article in response_global.json().get('articles', [])[:30]:
                 if article.get('title') and article['title'] != "[Removed]":
                    headlines.append({
                        "time": article.get("publishedAt", end_str),
                        "headline": article['title'],
                        "source": "NewsAPI-Global"
                    })
    except Exception as e:
        print(f"Error fetching from NewsAPI: {e}")
        
    return headlines

def get_alpaca_headers():
    accounts_path = os.path.join(os.path.dirname(__file__), 'alpaca_accounts.json')
    try:
        with open(accounts_path, 'r') as f:
            acc_data = json.load(f)
            for acc in acc_data.get('accounts', []):
                if acc.get('name') == 'Live Real Money':
                    return {
                        "APCA-API-KEY-ID": acc.get('key'),
                        "APCA-API-SECRET-KEY": acc.get('secret')
                    }
    except Exception as e:
        print(f"Failed to load alpaca credentials: {e}")
    return None


def fetch_macro_headlines(start_time, end_time):
    print("Fetching Alpaca (Macro) news...")
    headlines = []
    
    headers = get_alpaca_headers()
    if not headers:
        print("No Alpaca SIP credentials found.")
        return headlines
        
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    # NewsAPI endpoints like RFC3339 timestamps for start/end filtering.
    end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    try:
        # Fetch proxy macro info via ETFs
        url = "https://data.alpaca.markets/v1beta1/news"
        params = {
            "symbols": "SPY,QQQ,TLT",
            "start": start_str,
            "end": end_str,
            "limit": 30
        }
        
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            news = response.json().get('news', [])
            for article in news:
                title = article.get('headline', '')
                pub_time = article.get('created_at', end_str)
                if title:
                    headlines.append({
                        "time": pub_time,
                        "headline": title,
                        "source": "Alpaca-Macro"
                    })
        else:
             print(f"Error fetching Alpaca macro headlines: HTTP {response.status_code} {response.text}")
    except Exception as e:
        print(f"Error fetching Alpaca macro headlines: {e}")
        
    return headlines

def fetch_fred_data(target_date):
    print(f"Fetching Federal Reserve Economic Data (FRED) for {target_date}...")
    fred_series = {
        'DFF': 'Federal_Funds_Rate',
        'T10Y2Y': 'Yield_Curve_Spread',
        'WALCL': 'Fed_Total_Assets'
    }
    
    target_date_str = target_date.strftime('%Y-%m-%d')
    macro_data = {}
    
    try:
        for series_id, name in fred_series.items():
            # Request up to 5 days back to perfectly bridge weekends and holidays
            url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&observation_end={target_date_str}&limit=5"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for obs in data.get('observations', []):
                    val = obs.get('value')
                    if val != '.':
                        macro_data[name] = val
                        break
    except Exception as e:
        print(f"Error fetching FRED data for {target_date}: {e}")
        
    return macro_data

def fetch_options_data(target_date):
    print(f"Fetching Volatility and Options Data for {target_date}...")
    options_data = {}
    
    symbols = {
        'VIX': '^VIX',          # CBOE Volatility Index
        'VIX_3M': '^VIX3M',     # CBOE 3-Month Volatility
        'VIX_9D': '^VIX9D',     # CBOE 9-Day Volatility
        'SKEW': '^SKEW',        # CBOE SKEW Index
        'VVIX': '^VVIX'         # VIX of VIX
    }
    
    try:
        # Pull last 7 days to cover weekends and pick closest historical close
        start_date = (target_date - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        end_date = (target_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        for name, ticker_str in symbols.items():
            ticker = yf.Ticker(ticker_str)
            hist = ticker.history(start=start_date, end=end_date)
            if not hist.empty:
                # Filter to rows on or before the target_date
                valid_rows = hist[hist.index.tz_localize(None) <= pd.to_datetime(target_date)]
                if not valid_rows.empty:
                    last_val = valid_rows.iloc[-1]['Close']
                    options_data[name] = float(last_val)
                    
        # Calculate Term Structure (Backwardation vs Contango)
        if 'VIX_9D' in options_data and 'VIX_3M' in options_data:
            options_data['VIX_Term_Structure_Ratio'] = round(options_data['VIX_9D'] / options_data['VIX_3M'], 3)
            
    except Exception as e:
        print(f"Error fetching options/volatility data for {target_date}: {e}")
        
    return options_data

def score_world_state(headlines, fred_data, options_data, conn, target_date):
    target_date_str = target_date.strftime('%Y-%m-%d')
    
    if not headlines:
        print(f"[{target_date_str}] No headlines to score. Skipping.")
        return

    print(f"[{target_date_str}] Booting FinBERT to read global headlines...")
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert").to(device)
    model.eval()

    total_pos = 0.0
    total_neg = 0.0
    valid_articles = 0
    
    with torch.no_grad():
        for item in headlines:
            title = item['headline']
            inputs = tokenizer(title, padding=True, truncation=True, return_tensors='pt').to(device)
            outputs = model(**inputs)
            predictions = torch.nn.functional.softmax(outputs.logits, dim=-1)
            
            total_pos += predictions[0][0].item()
            total_neg += predictions[0][1].item()
            valid_articles += 1
            
    if valid_articles == 0:
        print(f"[{target_date_str}] No valid headlines parsed.")
        return
        
    avg_pos = total_pos / valid_articles
    avg_neg = total_neg / valid_articles
    polarity = avg_pos - avg_neg
    
    headlines_json = json.dumps(headlines)
    fred_json = json.dumps(fred_data)
    options_json = json.dumps(options_data)
    
    print(f"--- World State Record ({target_date_str}) ---")
    print(f"Processed {valid_articles} global headlines with precision timestamps.")
    print(f"Polarity Score: {polarity:.2f} | Positivity: {avg_pos:.2f} | Negativity: {avg_neg:.2f}")

    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO world_state_log 
        (date, sentiment_score, positive_prob, negative_prob, headlines, fred_data, options_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (target_date_str, polarity, avg_pos, avg_neg, headlines_json, fred_json, options_json))
    
    conn.commit()
    print(f"[{target_date_str}] State successfully logged to the Telemetry Hive.")

if __name__ == "__main__":
    conn = init_db()
    
    intervals, now_utc = get_missing_intervals()
    
    if not intervals:
        print("No missing intervals to process.")
    else:
        for idx, interval in enumerate(intervals):
            start = interval['start_time']
            end = interval['end_time']
            target_date = interval['target_date']
            
            print(f"\n=======================================================")
            print(f"PROCESSING INTERVAL: {start.strftime('%Y-%m-%dT%H:%M:%SZ')} TO {end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
            print(f"=======================================================")
            
            # Fetch NewsAPI specifically bounded by exact UTC marks
            newsapi_hl = fetch_newsapi_headlines(start, end)
            
            # Fetch Yahoo Finance bounded by exact UTC marks
            yahoo_hl = fetch_macro_headlines(start, end)
            
            # Combine 
            # We can't use python's `set()` directly on dicts. Let's deduplicate by headline text.
            all_headlines = []
            seen = set()
            for h in (newsapi_hl + yahoo_hl):
                if h['headline'] not in seen:
                    seen.add(h['headline'])
                    all_headlines.append(h)
            
            # Fetch FRED Macro Data for the end date target
            fred_data = fetch_fred_data(target_date)
            
            # Fetch Options/Vol Data
            options_data = fetch_options_data(target_date)
            
            # Score and Log
            score_world_state(all_headlines, fred_data, options_data, conn, target_date)
            
        update_checkpoint(now_utc)
        print("\nCheckpoint successfully updated. Pipeline Sleep Mode Initialized.")
        
    conn.close()
