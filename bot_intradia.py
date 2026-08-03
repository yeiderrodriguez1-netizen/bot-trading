# ============================================================
# BOT INTRADIA - opera varias veces al dia (cada 15 min via cron)
# Separado por completo del bot diario (bot_paper_trading.py): estado
# propio (portfolio_intradia.json), capital propio ($200), no toca
# ni depende de las 15 estrategias diarias.
#
# Universo: AAPL, MSFT, TSLA (solo en horario de mercado NYSE, con
# DST manejado automaticamente via zoneinfo) + BTC-USD (24/7, sin
# restriccion de horario).
#
# 2 estrategias intradia sobre velas de 15 minutos:
# EMA_RAPIDA - cruce alcista EMA9/EMA21 con RSI(14) no sobrecomprado
# RSI_SCALP  - RSI(7) saliendo de sobreventa (cruza 30 al alza)
#
# Solo envia Telegram cuando hay un evento real (compra/venta) para
# no saturar el chat con una notificacion cada 15 minutos.
# ============================================================
import os
import json
import yfinance as yf
import pandas as pd
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from bot_telegram import enviar_telegram, RIESGO

PORTFOLIO_FILE = "portfolio_intradia.json"
CAPITAL_INICIAL = 200
COMISION = 0.001
SLIPPAGE = 0.0005
VERSION = 1

TICKERS = ["AAPL", "MSFT", "TSLA", "BTC-USD"]
CRIPTO = {"BTC-USD"}  # 24/7, no se filtra por horario de mercado

# ============================================================
# HORARIO DE MERCADO (NYSE, con DST correcto via zoneinfo)
# ============================================================
def mercado_nyse_abierto():
    ahora_ny = datetime.now(ZoneInfo("America/New_York"))
    if ahora_ny.weekday() >= 5:  # sabado=5, domingo=6
        return False
    return dtime(9, 30) <= ahora_ny.time() <= dtime(16, 0)

def ahora_ny_str():
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")

# ============================================================
# DATOS E INDICADORES (velas de 15 minutos)
# ============================================================
def obtener_datos_intradia(ticker):
    df = yf.download(ticker, period="5d", interval="15m", auto_adjust=True,
                      multi_level_index=False, progress=False)
    return df.dropna().copy()

def calcular_indicadores_intradia(df):
    df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain14 = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss14 = loss.ewm(alpha=1/14, adjust=False).mean()
    df["RSI14"] = 100 - (100 / (1 + avg_gain14 / avg_loss14))
    avg_gain7 = gain.ewm(alpha=1/7, adjust=False).mean()
    avg_loss7 = loss.ewm(alpha=1/7, adjust=False).mean()
    df["RSI7"] = 100 - (100 / (1 + avg_gain7 / avg_loss7))
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()
    return df.dropna().copy()

# ============================================================
# ESTRATEGIAS INTRADIA
# ============================================================
def senal_ema_rapida(u, prev):
    return bool(prev["EMA9"] <= prev["EMA21"] and u["EMA9"] > u["EMA21"] and u["RSI14"] < 70)

def salida_ema_rapida(u, prev):
    return bool(u["EMA9"] < u["EMA21"])

def senal_rsi_scalp(u, prev):
    return bool(prev["RSI7"] < 30 and u["RSI7"] >= 30)

def salida_rsi_scalp(u, prev):
    return bool(u["RSI7"] > 70)

ESTRATEGIAS = {
    "EMA_RAPIDA": {"senal": senal_ema_rapida, "salida": salida_ema_rapida},
    "RSI_SCALP": {"senal": senal_rsi_scalp, "salida": salida_rsi_scalp},
}

# ============================================================
# ESTADO
# ============================================================
def estrategia_vacia():
    return {"capital": CAPITAL_INICIAL, "posiciones": {}, "historial": []}

def portafolio_nuevo():
    return {"version": VERSION, "estrategias": {n: estrategia_vacia() for n in ESTRATEGIAS}}

def cargar_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            data = json.load(f)
        if data.get("version") == VERSION:
            for n in ESTRATEGIAS:
                if n not in data["estrategias"]:
                    data["estrategias"][n] = estrategia_vacia()
            return data
    return portafolio_nuevo()

def guardar_portfolio(port):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(port, f, indent=2)

# ============================================================
# MECANICA DE TRADING (sin cola de "entra manana": ejecucion casi
# inmediata, acorde a que este bot sondea cada 15 minutos)
# ============================================================
def cerrar(p, nombre, ticker, precio_bruto, motivo, eventos):
    pos = p["posiciones"].pop(ticker)
    salida = precio_bruto * (1 - SLIPPAGE)
    pnl = pos["unidades"] * (salida - pos["entry"]) - pos["unidades"] * salida * COMISION
    p["capital"] += pos["unidades"] * pos["entry"] + pnl
    p["historial"].append({"ticker": ticker, "entrada": pos["entry"],
                            "salida": round(salida, 2), "pnl": round(pnl, 2),
                            "motivo": motivo, "hora": ahora_ny_str()})
    emoji = "✅" if pnl > 0 else "❌"
    eventos.append(f"{emoji} [{nombre}] {ticker} cerrada ({motivo}): ${pnl:+.2f}")

def gestionar_estrategia(nombre, est, p, ticker, u, prev, atr, eventos):
    precio = float(u["Close"])
    high_p, low_p = float(u["High"]), float(u["Low"])

    if ticker in p["posiciones"]:
        pos = p["posiciones"][ticker]
        if low_p <= pos["stop"]:
            cerrar(p, nombre, ticker, pos["stop"], "STOP", eventos)
        elif high_p >= pos["tp"]:
            cerrar(p, nombre, ticker, pos["tp"], "TAKE PROFIT", eventos)
        elif est["salida"](u, prev):
            cerrar(p, nombre, ticker, precio, "SALIDA", eventos)
        return

    if atr > 0 and est["senal"](u, prev):
        dist = 1.5 * atr
        fill = precio * (1 + SLIPPAGE)
        if dist > 0 and fill > 0:
            unidades = round(min(p["capital"] * RIESGO / dist, p["capital"] / fill), 6)
            if unidades * fill >= 1:
                costo = unidades * fill
                p["capital"] -= costo + costo * COMISION
                p["posiciones"][ticker] = {
                    "unidades": unidades, "entry": round(fill, 2),
                    "stop": round(fill - dist, 2),
                    "tp": round(fill + 2.5 * atr, 2), "hora": ahora_ny_str()}
                eventos.append(f"🟢 [{nombre}] {ticker} compra: "
                                f"{unidades} und @ ${fill:.2f}")

# ============================================================
# EJECUCION
# ============================================================
if __name__ == "__main__":
    port = cargar_portfolio()
    eventos = []
    cierres = {}
    mercado_abierto = mercado_nyse_abierto()

    for t in TICKERS:
        if t not in CRIPTO and not mercado_abierto:
            continue  # accion fuera de horario NYSE: se salta (la cripto no se filtra)
        try:
            df = obtener_datos_intradia(t)
            if len(df) < 30:
                continue
            df = calcular_indicadores_intradia(df)
            if len(df) < 2:
                continue
            u, prev = df.iloc[-1], df.iloc[-2]
            cierres[t] = float(u["Close"])
            atr = float(u["ATR"])
            for nombre, est in ESTRATEGIAS.items():
                gestionar_estrategia(nombre, est, port["estrategias"][nombre],
                                      t, u, prev, atr, eventos)
        except Exception as e:
            eventos.append(f"⚠ {t}: error ({e})")

    guardar_portfolio(port)

    if eventos:
        filas = []
        for nombre, p in port["estrategias"].items():
            equity = p["capital"] + sum(pos["unidades"] * cierres.get(tk, pos["entry"])
                                         for tk, pos in p["posiciones"].items())
            ret = (equity / CAPITAL_INICIAL - 1) * 100
            filas.append((nombre, equity, ret, len(p["posiciones"]), len(p["historial"])))
        filas.sort(key=lambda x: -x[1])

        lineas = [f"⚡ <b>INTRADIA</b> ({ahora_ny_str()} NY)",
                  f"Mercado NYSE: {'abierto' if mercado_abierto else 'cerrado (solo cripto)'}",
                  ""]
        for nombre, eq, ret, npos, ntr in filas:
            lineas.append(f"<b>{nombre}</b>: ${eq:.2f} ({ret:+.2f}%) | {npos} pos | {ntr} trades")
        lineas.append("\n<b>Evento:</b>")
        lineas += [f"  {e}" for e in eventos]
        lineas.append("\n<i>Dinero ficticio. Bot intradia, revisa cada 15 min.</i>")
        mensaje = "\n".join(lineas)
        print(mensaje)
        ok = enviar_telegram(mensaje)
        print("\nTelegram:", "enviado ✓" if ok else "NO enviado")
    else:
        print(f"[{ahora_ny_str()}] Sin movimientos este ciclo. No se envia Telegram.")
