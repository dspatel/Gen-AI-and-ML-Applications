import pandas as pd
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
import sqlite3
import os
import json
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'backtest_data.db')

class SentimentEngine:
    def __init__(self):
        print("Loading FinBERT NLP Model (ProsusAI/finbert)...")
        # FinBERT is specifically trained on corporate reports, earnings call transcripts, and financial news
        self.tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
        self.model = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
        self.conn = sqlite3.connect(DB_PATH)
        self.sentiment_history = {} # {symbol: DataFrame of daily sentiment scores}
        
    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS daily_sentiment (
                symbol TEXT,
                date TEXT,
                score REAL,
                PRIMARY KEY (symbol, date)
            )
        ''')
        self.conn.commit()

    def fetch_and_score_news(self, symbols):
        """
        Fetches recent news from yfinance across the pool and uses FinBERT to score it on the GPU.
        """
        self._create_tables()
        cur = self.conn.cursor()
        
        # 1. Hardware Definition (Target the RTX 4090)
        device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        print(f"Leveraging Hardware for NLP Engine: {device}")
        
        self.model = self.model.to(device)
        self.model.eval() # Set to inference mode
        
        # Load API keys to access SIP news
        accounts_path = os.path.join(os.path.dirname(__file__), 'alpaca_accounts.json')
        headers = {}
        try:
            with open(accounts_path, 'r') as f:
                acc_data = json.load(f)
                for acc in acc_data.get('accounts', []):
                    if acc.get('name') == 'Live Real Money':
                        headers = {
                            "APCA-API-KEY-ID": acc.get('key'),
                            "APCA-API-SECRET-KEY": acc.get('secret')
                        }
                        break
        except Exception as e:
            print(f"Failed to load alpaca credentials: {e}")
            return
            
        if not headers:
            print("No SIP credentials found in alpaca_accounts.json")
            return
        
        with torch.no_grad(): # Disable gradient calculation for faster inference
            for sym in symbols:
                print(f"Fetching news for {sym} via Alpaca...")
                try:
                    url = "https://data.alpaca.markets/v1beta1/news"
                    response = requests.get(url, params={"symbols": sym, "limit": 10}, headers=headers)
                    if response.status_code == 200:
                        news = response.json().get('news', [])
                    else:
                        print(f"API Error {response.status_code}: {response.text}")
                        continue
                except Exception as e:
                    print(f"Failed to fetch Alpaca news for {sym}: {e}")
                    continue
                    
                if not news: continue
                
                for article in news:
                    title = article.get('headline', '')
                    pub_time = article.get('created_at', '')
                    
                    if not title or not pub_time: continue
                    
                    try:
                        date_str = pd.to_datetime(pub_time).strftime('%Y-%m-%d')
                            
                        # Run NLP Inference on GPU
                        inputs = self.tokenizer(title, padding=True, truncation=True, return_tensors='pt').to(device)
                        outputs = self.model(**inputs)
                        predictions = torch.nn.functional.softmax(outputs.logits, dim=-1)
                        
                        # FinBERT returns [Positive, Negative, Neutral]
                        pos_score = predictions[0][0].item()
                        neg_score = predictions[0][1].item()
                        neu_score = predictions[0][2].item()
                        
                        # Calculate Polarity (-1 to 1)
                        if neu_score > 0.6:
                            polarity = 0.0
                        else:
                            polarity = pos_score - neg_score
                            
                        cur.execute('''
                            INSERT OR REPLACE INTO daily_sentiment (symbol, date, score)
                            VALUES (?, ?, ?)
                        ''', (sym, date_str, polarity))
                        
                    except Exception as e:
                        print(f"Error parsing news for {sym}: {e}")
                        continue
                        
        self.conn.commit()
        
    def load_sentiment_data(self):
        """Loads the pre-calculated daily sentiment scores from the database into memory."""
        print("Loading Sentiment History...")
        df = pd.read_sql("SELECT * FROM daily_sentiment ORDER BY date", self.conn)
        df['date'] = pd.to_datetime(df['date'])
        
        for sym, grp in df.groupby('symbol'):
            grp = grp.set_index('date').sort_index()
            # Calculate a rolling 7-day sentiment score (news takes a few days to digest)
            grp['rolling_score'] = grp['score'].rolling(window='7D', min_periods=1).mean()
            self.sentiment_history[sym] = grp
            
        return self.sentiment_history
            
    def get_sentiment(self, symbol, current_date):
        """
        Returns the rolling NLP sentiment score for a symbol on a specific date.
        Range is -1 (Extremely Bearish / Bad News) to +1 (Extremely Bullish / Good News)
        """
        if symbol not in self.sentiment_history:
            return 0.0
            
        df = self.sentiment_history[symbol]
        
        if current_date in df.index:
            return df.loc[current_date, 'rolling_score']
            
        # Fallback to most recent past news
        past = df[df.index <= current_date]
        if past.empty:
            return 0.0
            
        return past.iloc[-1]['rolling_score']

if __name__ == "__main__":
    symbols = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 
               'AMD', 'NFLX', 'AVGO', 'CRM', 'ADBE', 'ORCL', 'INTC', 'QCOM', 'SPY']
    nlp = SentimentEngine()
    print("\nStarting News Pipeline...")
    nlp.fetch_and_score_news(symbols)
    nlp.load_sentiment_data()
    
    print("\nLatest NLP Sentiment Scores:")
    for sym in symbols:
        try:
            latest_date = nlp.sentiment_history[sym].index[-1]
            score = nlp.get_sentiment(sym, latest_date)
            
            if score > 0.3: vibe = "BULLISH"
            elif score < -0.3: vibe = "BEARISH"
            else: vibe = "NEUTRAL"
            
            print(f"{sym:>5} | Date: {latest_date.date()} | Score: {score:>+5.2f} | Status: {vibe}")
        except:
            print(f"{sym:>5} | No recent news found.")
