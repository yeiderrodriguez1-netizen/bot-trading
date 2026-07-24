# ============================================================
# BOT DE TRADING + IA + SENTIMIENTO -> SENAL POR TELEGRAM
# Corre en la nube (GitHub Actions) y envia la senal al celular.
# Requiere variables de entorno: TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
# ============================================================
import os
import json
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import precision_score
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ============================================================
# CONFIGURACION
# ============================================================
TICKERS = ["AAPL", "MSFT", "TSLA", "BTC-USD"]
CAPITAL = 200      # capital maximo a invertir por ahora
RIESGO = 0.02
UMBRAL_IA = 0.55
UMBRAL_SENT = -0.05
HORIZONTE = 5
OBJETIVO = 0.02
FEATURES = ["retorno1", "retorno5", "tendencia", "volatilidad", "vol_rel", "RSI"]

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


# ============================================================
# TELEGRAM
# ============================================================
def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID. Mensaje no enviado:")
        print(mensaje)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read()).get("ok", False)
    except Exception as e:
        print(f"Error enviando a Telegram: {e}")
        return False


# ============================================================
# DATOS E INDICADORES
# ============================================================
def obtener_datos(ticker):
    df = yf.download(ticker, period="5y", interval="1d", auto_adjust=True,
                     multi_level_index=False, progress=False)
    return df.dropna().copy()


def calcular_indicadores(df):
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    df["RSI"] = 100 - (100 / (1 + avg_gain / avg_loss))

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()

    df["retorno1"] = df["Close"].pct_change()
    df["retorno5"] = df["Close"].pct_change(5)
    df["tendencia"] = (df["SMA20"] - df["SMA50"]) / df["Close"]
    df["volatilidad"] = df["ATR"] / df["Close"]
    df["vol_rel"] = df["Volume"] / df["Volume"].rolling(20).mean()
    return df.dropna().copy()


# ============================================================
# IA (validacion out-of-sample + modelo final para la senal en vivo)
# ============================================================
def entrenar_IA(df):
    datos = df.copy()
    futuro = (datos["Close"].shift(-HORIZONTE) / datos["Close"]) - 1
    datos["target"] = (futuro > OBJETIVO).astype(int)
    datos = datos.iloc[:-HORIZONTE]

    X, y = datos[FEATURES], datos["target"]
    corte = int(len(X) * 0.8)
    params = dict(n_estimators=300, max_depth=5,
                  class_weight="balanced", random_state=42)

    modelo_val = RandomForestClassifier(**params).fit(X.iloc[:corte], y.iloc[:corte])
    prob_test = modelo_val.predict_proba(X.iloc[corte:])[:, 1]
    pred = (prob_test > UMBRAL_IA).astype(int)
    precision = precision_score(y.iloc[corte:], pred, zero_division=0)
    tasa_base = y.iloc[corte:].mean()

    modelo = RandomForestClassifier(**params).fit(X, y)
    return modelo, precision, tasa_base


# ============================================================
# SENTIMIENTO (None si falla -> no bloquea)
# ============================================================
def analizar_sentimiento(ticker):
    query = ticker.replace("-USD", "") + "+stock"
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(urllib.request.urlopen(req, timeout=10).read())
        titulares = [i.find('title').text for i in root.findall('.//item')[:10]
                     if i.find('title') is not None]
        if not titulares:
            return None
        a = SentimentIntensityAnalyzer()
        return float(np.mean([a.polarity_scores(t)["compound"] for t in titulares]))
    except Exception:
        return None


# ============================================================
# SENAL Y RIESGO
# ============================================================
def gestion_riesgo(precio, atr, capital=CAPITAL, riesgo=RIESGO, fraccional=False):
    dist = 2 * atr
    if dist <= 0 or precio <= 0:
        return None
    unidades = min(capital * riesgo / dist, capital / precio)
    if not fraccional:
        unidades = int(unidades)
    if unidades <= 0:
        return None
    return {"unidades": round(unidades, 6), "stop": round(precio - dist, 2),
            "take": round(precio + 2 * dist, 2),
            "riesgo_usd": round(unidades * dist, 2)}


def analizar_ticker(ticker):
    df = obtener_datos(ticker)
    if len(df) < 120:
        return f"⚠ <b>{ticker}</b>: datos insuficientes."
    df = calcular_indicadores(df)
    modelo, precision, tasa_base = entrenar_IA(df)
    df["IA_PROB"] = modelo.predict_proba(df[FEATURES])[:, 1]

    u = df.iloc[-1]
    tendencia = u["SMA20"] > u["SMA50"]
    breakout = u["Close"] > df["High"].rolling(20).max().shift(1).iloc[-1]
    ia_ok = u["IA_PROB"] > UMBRAL_IA
    senal = bool(tendencia and breakout and ia_ok)

    sentimiento = analizar_sentimiento(ticker)
    bloqueo = sentimiento is not None and sentimiento < UMBRAL_SENT

    fecha = u.name.strftime('%Y-%m-%d')
    sent_txt = f"{sentimiento:.2f}" if sentimiento is not None else "N/D"
    lineas = [f"<b>{ticker}</b> ({fecha})",
              f"Precio: ${float(u['Close']):.2f} | IA: {u['IA_PROB']*100:.0f}% "
              f"(prec. {precision*100:.0f}% vs base {tasa_base*100:.0f}%) | Sent: {sent_txt}"]

    if senal and not bloqueo:
        r = gestion_riesgo(float(u["Close"]), float(u["ATR"]),
                           fraccional="-USD" in ticker)
        if r:
            lineas.append(f"🟢 <b>COMPRA</b> | {r['unidades']} und | "
                          f"SL ${r['stop']} | TP ${r['take']} | "
                          f"Riesgo ${r['riesgo_usd']}")
        else:
            lineas.append("🟡 Señal sin tamaño de posición válido")
    elif senal and bloqueo:
        lineas.append("🔴 Señal BLOQUEADA por sentimiento negativo")
    else:
        lineas.append("🟡 Sin operación")
    return "\n".join(lineas)


# ============================================================
# EJECUCION
# ============================================================
if __name__ == "__main__":
    bloques = []
    for t in TICKERS:
        try:
            bloques.append(analizar_ticker(t))
        except Exception as e:
            bloques.append(f"⚠ <b>{t}</b>: error ({e})")

    mensaje = "📊 <b>SEÑALES DEL DÍA</b>\n\n" + "\n\n".join(bloques)
    mensaje += "\n\n<i>Solo informativo. No es asesoría financiera.</i>"
    print(mensaje)
    ok = enviar_telegram(mensaje)
    print("\nTelegram:", "enviado ✓" if ok else "NO enviado")
