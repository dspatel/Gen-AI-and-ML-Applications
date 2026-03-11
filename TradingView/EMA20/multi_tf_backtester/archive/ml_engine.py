import pandas as pd
import numpy as np
import xgboost as xgb
import os
from signal_engines import PortfolioDataEngine, compute_sma, compute_ema, compute_rsi, compute_macd

class MLEngine:
    def __init__(self):
        self.model = None
        self.features = [
            'ret_1d', 'ret_5d', 'ret_20d', 'ret_60d',
            'dist_ema20', 'dist_sma50', 'dist_sma200',
            'rsi_14', 'macd_hist_norm', 'atr_norm'
        ]

    def engineer_features(self, df):
        """Engineers technical features for the ML model."""
        df = df.copy()
        
        # Returns
        df['ret_1d'] = df['close'].pct_change(1)
        df['ret_5d'] = df['close'].pct_change(5)
        df['ret_20d'] = df['close'].pct_change(20)
        df['ret_60d'] = df['close'].pct_change(60)
        
        # Distances to Moving Averages
        ema_20 = compute_ema(df['close'], 20)
        sma_50 = compute_sma(df['close'], 50)
        sma_200 = compute_sma(df['close'], 200)
        
        df['dist_ema20'] = (df['close'] - ema_20) / ema_20
        df['dist_sma50'] = (df['close'] - sma_50) / sma_50
        df['dist_sma200'] = (df['close'] - sma_200) / sma_200
        
        # Oscillators
        df['rsi_14'] = compute_rsi(df['close'], 14)
        _, _, macd_hist = compute_macd(df['close'])
        df['macd_hist_norm'] = macd_hist / df['close']
        
        # Volatility
        atr = df['high'].rolling(14).max() - df['low'].rolling(14).min()
        df['atr_norm'] = atr / df['close']
        
        # Target: Forward 20-day return (1 month trend)
        df['fwd_ret_20d'] = df['close'].shift(-20) / df['close'] - 1
        
        return df

    def prepare_dataset(self, data_engine, start_date='2018-01-01', end_date='2020-12-31'):
        """Creates the training dataset by combining all symbols and comparing to SPY."""
        print(f"Preparing ML Training Data from {start_date} to {end_date}...")
        
        # First engineer features for SPY to get baseline targets
        spy_df = self.engineer_features(data_engine.daily['SPY'])
        spy_df = spy_df[(spy_df.index >= start_date) & (spy_df.index <= end_date)].copy()
        
        all_data = []
        for sym, df in data_engine.daily.items():
            if sym == 'SPY': continue
            
            sym_df = self.engineer_features(df)
            sym_df = sym_df[(sym_df.index >= start_date) & (sym_df.index <= end_date)].copy()
            
            # Target: Did it outperform SPY over the next 20 days?
            # 1 = Yes (Alpha generation), 0 = No
            sym_df = sym_df.join(spy_df[['fwd_ret_20d']], rsuffix='_spy')
            sym_df['target'] = (sym_df['fwd_ret_20d'] > sym_df['fwd_ret_20d_spy']).astype(int)
            
            sym_df['symbol'] = sym
            all_data.append(sym_df)
            
        combined = pd.concat(all_data)
        combined.dropna(subset=self.features + ['target'], inplace=True)
        return combined

    def train_model(self, data_engine, train_start='2018-01-01', train_end='2020-12-31'):
        """Trains the XGBoost Classifier on historical outperformance."""
        train_df = self.prepare_dataset(data_engine, train_start, train_end)
        
        X = train_df[self.features]
        y = train_df['target']
        
        print(f"Training XGBoost on {len(X)} samples. Base Win Rate: {y.mean()*100:.1f}%...")
        
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=-1
        )
        
        self.model.fit(X, y)
        print("Model Training Complete.")
        
        # Optional: Print Feature Importance
        importances = pd.Series(self.model.feature_importances_, index=self.features)
        print("\nTop 3 Important Features:")
        print(importances.sort_values(ascending=False).head(3))

    def score_symbols(self, current_date, data_engine):
        """
        Drop-in replacement for RulesEngine.score_symbols.
        Uses the trained ML model to output a probability score (0 to 1).
        """
        if self.model is None:
            raise ValueError("Model has not been trained yet. Call train_model() first.")
            
        scores = []
        for sym, df in data_engine.daily.items():
            if sym == 'SPY': continue
            
            past = df[df.index <= current_date]
            if len(past) < 200: continue # Need enough history for MA features
            
            # Engineer features on the fly for the current snapshot
            # To be efficient, we slice the last 200 rows 
            snapshot = past.iloc[-200:].copy()
            features_df = self.engineer_features(snapshot)
            current_features = features_df.iloc[[-1]] # The last row is today
            
            # Ensure no NaNs in features
            if current_features[self.features].isna().any().any():
                continue
                
            X_pred = current_features[self.features]
            
            # Probability that target is 1 (Outperforms SPY)
            prob = self.model.predict_proba(X_pred)[0][1]
            
            scores.append({
                'symbol': sym,
                'score': prob, # Score is pure machine learning probability
                'close': current_features.iloc[0]['close'],
                'rsi': current_features.iloc[0]['rsi_14'],
                'atr': current_features.iloc[0]['atr_norm'] * current_features.iloc[0]['close']
            })
            
        scoring_df = pd.DataFrame(scores)
        if scoring_df.empty: return pd.DataFrame()
        
        # Sort by probability descending
        scoring_df = scoring_df.sort_values(by='score', ascending=False)
        return scoring_df

if __name__ == "__main__":
    db = PortfolioDataEngine()
    db.load_all_data()
    
    ml = MLEngine()
    ml.train_model(db, train_start='2018-01-01', train_end='2020-12-31')
    
    test_date = pd.to_datetime('2024-03-01')
    print(f"\n--- ML Probabilities for {test_date.date()} ---")
    leaderboard = ml.score_symbols(test_date, db)
    print(leaderboard.head(5).to_string(index=False))
