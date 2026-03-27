import yfinance as yf
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import sqlite3
import pandas as pd
import datetime
import os
import json
import requests
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# STRICLY ISOLATED TO OMEGA FOLDER
DB_PATH = os.path.join(os.path.dirname(__file__), 'omega_telemetry.db')
CHECKPOINT_PATH = os.path.join(os.path.dirname(__file__), 'omega_macro_checkpoint.txt')

NEWS_API_KEY = "19164633a36e4acda78e65ea0cda5fc9"
FRED_API_KEY = "6edacb8953cb602d0e88ba8693891f9d"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Unique table for Omega's world state memory
    cur.execute('''
        CREATE TABLE IF NOT EXISTS world_state_log (
            date TEXT PRIMARY KEY,
            sentiment_score REAL,
            positive_prob REAL,
            negative_prob REAL,
            headlines JSON,
            fred_data JSON,
            options_data JSON
        )
    ''')
    conn.commit()
    return conn

def get_missing_intervals():
    now_utc = datetime.datetime.utcnow()
    
    if os.path.exists(CHECKPOINT_PATH):
        with open(CHECKPOINT_PATH, 'r') as f:
            last_dt_str = f.read().strip()
            try:
                last_dt = datetime.datetime.fromisoformat(last_dt_str.replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                last_dt = now_utc - datetime.timedelta(days=1)
    else:
        logger.info("No checkpoint found. Initializing deep 14-day Omega Macro Backfill...")
        last_dt = now_utc - datetime.timedelta(days=14)
        
    intervals = []
    current_start = last_dt
    
    while current_start < now_utc:
        current_end = current_start + datetime.timedelta(hours=24)
        if current_end > now_utc:
            current_end = now_utc
            
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
    logger.info(f"Fetching NewsAPI ({start_str} to {end_str})...")
    headlines = []
    
    try:
        # 1. Global Financial & Economic News
        query_econ = "(business OR economy OR markets) AND (US OR global)"
        url_biz = f"https://newsapi.org/v2/everything?q={query_econ}&from={start_str}&to={end_str}&language=en&sortBy=popularity&apiKey={NEWS_API_KEY}"
        
        response = requests.get(url_biz, timeout=10)
        if response.status_code == 200:
            for article in response.json().get('articles', [])[:20]:
                if article.get('title') and article['title'] != "[Removed]":
                    headlines.append({
                        "time": article.get("publishedAt", end_str),
                        "headline": article['title'],
                        "source": "NewsAPI-Business"
                    })
                    
        # 2. General World Context (Wars, Politics, Natural Disasters, Pandemics)
        # We explicitly exclude sports/entertainment to keep the AI focused on structural world events
        query_world = "(world OR international OR politics OR crisis OR war OR election OR disaster OR health OR geopolitics) NOT (sports OR entertainment OR celebrity OR movie)"
        url_global = f"https://newsapi.org/v2/everything?q={query_world}&from={start_str}&to={end_str}&language=en&sortBy=popularity&apiKey={NEWS_API_KEY}"
        
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
        logger.error(f"Error fetching from NewsAPI: {e}")
        
    return headlines

def get_alpaca_headers():
    # Use Omega specifically isolated keys
    accounts_path = os.path.join(os.path.dirname(__file__), 'omega_keys.json')
    try:
        with open(accounts_path, 'r') as f:
            acc_data = json.load(f)
            for acc in acc_data.get('accounts', []):
                # Omega uses Paper account
                if 'Paper' in acc.get('name', ''):
                    return {
                        "APCA-API-KEY-ID": acc.get('key'),
                        "APCA-API-SECRET-KEY": acc.get('secret')
                    }
    except Exception as e:
        logger.error(f"Failed to load omega credentials: {e}")
    return None

def fetch_alpaca_news(start_time, end_time):
    logger.info("Fetching Alpaca Macro news...")
    headlines = []
    
    headers = get_alpaca_headers()
    if not headers:
        logger.error("No Alpaca credentials found.")
        return headlines
        
    start_str = start_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_str = end_time.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    try:
        url = "https://data.alpaca.markets/v1beta1/news"
        params = {"symbols": "SPY,QQQ,TLT", "start": start_str, "end": end_str, "limit": 30}
        
        response = requests.get(url, params=params, headers=headers)
        if response.status_code == 200:
            news = response.json().get('news', [])
            for article in news:
                title = article.get('headline', '')
                pub_time = article.get('created_at', end_str)
                if title:
                    headlines.append({"time": pub_time, "headline": title, "source": "Alpaca-Macro"})
        else:
             logger.error(f"Error fetching Alpaca macro headlines: HTTP {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Error fetching Alpaca macro headlines: {e}")
        
    return headlines

def fetch_fred_data(target_date):
    logger.info(f"Fetching FRED Data for {target_date}...")
    fred_series = {'DFF': 'Federal_Funds_Rate', 'T10Y2Y': 'Yield_Curve_Spread', 'WALCL': 'Fed_Total_Assets'}
    target_date_str = target_date.strftime('%Y-%m-%d')
    macro_data = {}
    
    try:
        for series_id, name in fred_series.items():
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
        logger.error(f"Error fetching FRED data for {target_date}: {e}")
        
    return macro_data

def score_world_state(headlines, fred_data, conn, target_date):
    target_date_str = target_date.strftime('%Y-%m-%d')
    
    if not headlines:
        logger.info(f"[{target_date_str}] No headlines to score. Skipping.")
        return

    logger.info(f"[{target_date_str}] Booting FinBERT NLP Model...")
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
        return
        
    avg_pos = total_pos / valid_articles
    avg_neg = total_neg / valid_articles
    polarity = avg_pos - avg_neg
    
    headlines_json = json.dumps(headlines)
    fred_json = json.dumps(fred_data)
    options_json = "{}" # Not actively fetched here to reduce VIX yfinance collision; handled cleanly in Omega pipeline
    
    logger.info(f"Processed {valid_articles} global headlines with polarity: {polarity:.2f}")

    cur = conn.cursor()
    cur.execute('''
        INSERT OR REPLACE INTO world_state_log 
        (date, sentiment_score, positive_prob, negative_prob, headlines, fred_data, options_data)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (target_date_str, polarity, avg_pos, avg_neg, headlines_json, fred_json, options_json))
    
    conn.commit()
    logger.info(f"[{target_date_str}] Successfully injected isolated Omega Macro State into omega_telemetry.db")

def run_omega_macro_backfill():
    conn = init_db()
    intervals, now_utc = get_missing_intervals()
    
    if not intervals:
        logger.info("Omega Macro State is perfectly up to date.")
    else:
        for idx, interval in enumerate(intervals):
            start = interval['start_time']
            end = interval['end_time']
            target_date = interval['target_date']
            
            logger.info(f"\n--- PROCESSING INTERVAL: {start.strftime('%Y-%m-%d')} ---")
            
            newsapi_hl = fetch_newsapi_headlines(start, end)
            alpaca_hl = fetch_alpaca_news(start, end)
            
            all_headlines = []
            seen = set()
            for h in (newsapi_hl + alpaca_hl):
                if h['headline'] not in seen:
                    seen.add(h['headline'])
                    all_headlines.append(h)
            
            fred_data = fetch_fred_data(target_date)
            score_world_state(all_headlines, fred_data, conn, target_date)
            
        update_checkpoint(now_utc)
        logger.info("\nOmega Macro Backfill Complete. Sleep Mode Initialized.")
        
    conn.close()

if __name__ == "__main__":
    run_omega_macro_backfill()
