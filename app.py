import streamlit as st
import websocket
import json
import pandas as pd
import ta
import time
import numpy as np

# Page configuration
st.set_page_config(
    page_title="KHOURYBOT - Chart Analysis Bot",
    page_icon="https://i.imgur.com/KHOURYBOT_Logo.png"
)

# Function to convert ticks to OHLC candles based on tick count
def ticks_to_ohlc_by_count(ticks_df, tick_count):
    if ticks_df.empty:
        return pd.DataFrame()

    ohlc_data = []
    
    prices = ticks_df['price'].values
    timestamps = ticks_df['timestamp'].values
    
    for i in range(0, len(prices), tick_count):
        chunk = prices[i:i + tick_count]
        if len(chunk) == tick_count:
            open_price = chunk[0]
            high_price = np.max(chunk)
            low_price = np.min(chunk)
            close_price = chunk[-1]
            ohlc_data.append({
                'timestamp': timestamps[i+tick_count-1],
                'Open': open_price,
                'High': high_price,
                'Low': low_price,
                'Close': close_price,
                'Volume': tick_count
            })
            
    ohlc_df = pd.DataFrame(ohlc_data)
    if not ohlc_df.empty:
        ohlc_df['timestamp'] = pd.to_datetime(ohlc_df['timestamp'], unit='s')
        ohlc_df.set_index('timestamp', inplace=True)
    
    return ohlc_df

# Function to find support and resistance based on reversal patterns
def find_support_resistance(data):
    supports = []
    resistances = []
    
    for i in range(1, len(data) - 1):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]
        next_candle = data.iloc[i+1]
        
        # Support condition: strong bearish candle followed by a strong bullish candle
        if prev_candle['Close'] < prev_candle['Open'] and next_candle['Close'] > next_candle['Open']:
            supports.append(current_candle['Low'])
            
        # Resistance condition: strong bullish candle followed by a strong bearish candle
        if prev_candle['Close'] > prev_candle['Open'] and next_candle['Close'] < next_candle['Open']:
            resistances.append(current_candle['High'])
            
    # Keep only the most significant levels
    supports = sorted(list(set(supports)), reverse=True)[:5]
    resistances = sorted(list(set(resistances)))[:5]
    
    return supports, resistances

# Function to analyze candlestick patterns
def analyze_candlesticks(data):
    score = 0
    
    if len(data) >= 2:
        last = data.iloc[-1]
        prev = data.iloc[-2]
        
        # Bullish Engulfing
        if (last['Close'] > last['Open'] and prev['Close'] < prev['Open'] and
            last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score += 30
        
        # Bearish Engulfing
        if (last['Close'] < last['Open'] and prev['Close'] > prev['Open'] and
            last['High'] > prev['High'] and last['Low'] < prev['Low']):
            score -= 30

        # Hammer & Shooting Star
        body = abs(last['Close'] - last['Open'])
        lower_shadow = last['Open'] - last['Low'] if last['Open'] > last['Close'] else last['Close'] - last['Low']
        upper_shadow = last['High'] - last['Close'] if last['Open'] > last['Close'] else last['High'] - last['Open']
        
        if last['Close'] > last['Open'] and lower_shadow > body * 2 and upper_shadow < body:
            score += 20
        
        if last['Close'] < last['Open'] and upper_shadow > body * 2 and lower_shadow < body:
            score -= 20
            
    return score

# Data analysis function
def analyse_data(data):
    try:
        if data.empty or len(data) < 50:
            return None, "Error: Insufficient data for analysis (less than 50 candles)."

        data = data.tail(50).copy()

        # Add quick indicators
        data['RSI'] = ta.momentum.RSIIndicator(data['Close']).rsi()
        data['Stoch_K'] = ta.momentum.StochasticOscillator(data['High'], data['Low'], data['Close']).stoch()
        data['ROC'] = ta.momentum.ROCIndicator(data['Close']).roc()
        
        # Add ADX indicator
        adx_indicator = ta.trend.ADXIndicator(data['High'], data['Low'], data['Close'])
        data['ADX'] = adx_indicator.adx()
        data['ADX_pos'] = adx_indicator.adx_pos()
        data['ADX_neg'] = adx_indicator.adx_neg()
        
        # Add MACD indicator
        macd_indicator = ta.trend.MACD(data['Close'])
        data['MACD'] = macd_indicator.macd()
        data['MACD_signal'] = macd_indicator.macd_signal()
        
        # Add Ichimoku Cloud indicator
        ichimoku_indicator = ta.trend.IchimokuIndicator(data['High'], data['Low'])
        data['ichimoku_base_line'] = ichimoku_indicator.ichimoku_base_line()
        data['ichimoku_conversion_line'] = ichimoku_indicator.ichimoku_conversion_line()
        data['ichimoku_a'] = ichimoku_indicator.ichimoku_a()
        data['ichimoku_b'] = ichimoku_indicator.ichimoku_b()
        
        # Add Moving Averages (EMA)
        data['ema10'] = ta.trend.EMAIndicator(data['Close'], window=10).ema_indicator()
        data['ema20'] = ta.trend.EMAIndicator(data['Close'], window=20).ema_indicator()


        # --- Scoring System (based on indicators) ---
        score = 0
        
        # 1. Candlestick Patterns
        candlestick_score = analyze_candlesticks(data)
        score += candlestick_score
        
        # 2. Support and Resistance Analysis
        supports, resistances = find_support_resistance(data)
        last_close = data.iloc[-1]['Close']
        
        for support in supports:
            if abs(last_close - support) / support < 0.0001: 
                score += 40
            elif last_close < support:
                score -= 50
        
        for resistance in resistances:
            if abs(last_close - resistance) / resistance < 0.0001:
                score -= 40
            elif last_close > resistance:
                score += 50
        
        # 3. Quick Indicators (RSI, Stoch, ROC)
        if data['RSI'].iloc[-1] > 70: score -= 20
        elif data['RSI'].iloc[-1] < 30: score += 20
        
        if data['Stoch_K'].iloc[-1] > 80: score -= 20
        elif data['Stoch_K'].iloc[-1] < 20: score += 20

        if data['ROC'].iloc[-1] > 0: score += 10
        elif data['ROC'].iloc[-1] < 0: score -= 10
        
        # 4. ADX Indicator
        if data['ADX'].iloc[-1] > 25:
            if data['ADX_pos'].iloc[-1] > data['ADX_neg'].iloc[-1]: score += 30
            elif data['ADX_neg'].iloc[-1] > data['ADX_pos'].iloc[-1]: score -= 30

        # 5. MACD Indicator
        if data['MACD'].iloc[-1] > data['MACD_signal'].iloc[-1]: score += 25
        elif data['MACD'].iloc[-1] < data['MACD_signal'].iloc[-1]: score -= 25
        
        # 6. Ichimoku Indicator
        last_close_ichimoku = data.iloc[-1]['Close']
        cloud_a = data.iloc[-1]['ichimoku_a']
        cloud_b = data.iloc[-1]['ichimoku_b']
        
        if last_close_ichimoku > max(cloud_a, cloud_b): score += 40
        elif last_close_ichimoku < min(cloud_a, cloud_b): score -= 40
        
        # 7. Moving Averages (EMA)
        if len(data) >= 20:
            if data['ema10'].iloc[-1] > data['ema20'].iloc[-1]: score += 20
            elif data['ema10'].iloc[-1] < data['ema20'].iloc[-1]: score -= 20
            
            if last_close > data['ema20'].iloc[-1] and last_close > data['ema10'].iloc[-1]: score += 20
            elif last_close < data['ema20'].iloc[-1] and last_close < data['ema10'].iloc[-1]: score -= 20


        # --- Provisional Decision ---
        provisional_decision = ""
        if score > 0:
            provisional_decision = "Buy"
        elif score < 0:
            provisional_decision = "Sell"
        else:
            provisional_decision = "Neutral"

        # --- Final Signal Logic (REVERSED as per your request) ---
        if len(data) >= 1:
            last_candle = data.iloc[-1]
            last_candle_is_up = last_candle['Close'] > last_candle['Open']
            
            # If indicators and last candle point in the same direction, output "No Signal"
            if (provisional_decision == "Buy" and last_candle_is_up) or \
               (provisional_decision == "Sell" and not last_candle_is_up):
                return "Neutral", None # Returning Neutral to represent "No Signal"
            # Otherwise, give the signal
            elif (provisional_decision == "Buy" and not last_candle_is_up) or \
                 (provisional_decision == "Sell" and last_candle_is_up):
                return provisional_decision, None
            else:
                return "Neutral", None
        else:
            return provisional_decision, None
        
    except Exception as e:
        return None, f"An error occurred during analysis: {e}"

# WebSocket connection function
def fetch_data_from_websocket(symbol, count, max_retries=3):
    retries = 0
    while retries < max_retries:
        try:
            ws = websocket.create_connection("wss://blue.derivws.com/websockets/v3?app_id=16929")
            request = {
                "ticks_history": symbol,
                "end": "latest",
                "count": count * 50,
                "style": "ticks"
            }
            ws.send(json.dumps(request))
            response = json.loads(ws.recv())
            ws.close()
            
            if 'history' in response:
                ticks = response['history']['prices']
                timestamps = response['history']['times']
                df = pd.DataFrame({'timestamp': timestamps, 'price': ticks})
                return df
            else:
                retries += 1
                time.sleep(1)
        except Exception as e:
            retries += 1
            time.sleep(1)
    st.error("Failed to fetch data after several attempts.")
    return pd.DataFrame()


# --- Streamlit UI ---
st.title("KHOURYBOT 🤖")
st.markdown("---")

col1, col2 = st.columns(2)

with col1:
    symbol_map = {'EUR/USD': 'frxEURUSD', 'EUR/GBP': 'frxEURGBP', 'EUR/JPY': 'frxEURJPY'}
    selected_pair_name = st.selectbox('Select Currency Pair:', options=list(symbol_map.keys()))
    selected_symbol = symbol_map[selected_pair_name]

if st.button('Get Signal Now'):
    with st.spinner('Analyzing...'):
        ticks_data = fetch_data_from_websocket(selected_symbol, count=50) 
        if ticks_data.empty:
            st.error("Failed to fetch data. Please try again later.")
        else:
            candles_5ticks = ticks_to_ohlc_by_count(ticks_data, 5)
            entry_signal, error = analyse_data(candles_5ticks) 
            
            st.markdown("---")
            
            if error:
                st.error(error)
            elif entry_signal == "Neutral":
                st.warning("No signal currently.")
            else:
                st.success(f"🎉 The signal is: **{entry_signal}**.")
