# ============================================================
# PAPER TRADING MULTI-ESTRATEGIA
# 3 estrategias compiten en paralelo con $200 ficticios cada una:
#   IA+SENT   - breakout + IA + filtro de sentimiento (la original)
#   TECNICA   - breakout puro, sin IA (¿la IA aporta algo?)
#   REVERSION - comprar caidas (RSI bajo) dentro de tendencia alcista
# Mismas reglas de riesgo para todas -> comparacion justa.
# Estado en portfolio.json (se commitea al repo). Reporte a Telegram.
# ============================================================
import os
import json
from bot_telegram import (obtener_datos, calcular_indicadores, entrenar_IA,
                          analizar_sentimiento, enviar_telegram,
                          FEATURES, UMBRAL_IA, UMBRAL_SENT, TICKERS, RIESGO)

PORTFOLIO_FILE = "portfolio.json"
CAPITAL_INICIAL = 200
COMISION = 0.001
SLIPPAGE = 0.0005
VERSION = 2


# ============================================================
# ESTRATEGIAS (senal de entrada; opcionalmente senal de salida)
# ============================================================
def senal_ia(u, ctx):
    if not (u["SMA20"] > u["SMA50"] and ctx["breakout"]):
        return False
    if ctx["ia_prob"] is None or ctx["ia_prob"] <= UMBRAL_IA:
        return False
    s = ctx["sentimiento"]
    return not (s is not None and s < UMBRAL_SENT)


def senal_tecnica(u, ctx):
    return bool(u["SMA20"] > u["SMA50"] and ctx["breakout"])


def senal_reversion(u, ctx):
    return bool(u["SMA20"] > u["SMA50"] and u["RSI"] < 35)


def salida_reversion(prev):
    return bool(prev["RSI"] > 60)


ESTRATEGIAS = {
    "IA+SENT": {"senal": senal_ia, "salida": None},
    "TECNICA": {"senal": senal_tecnica, "salida": None},
    "REVERSION": {"senal": senal_reversion, "salida": salida_reversion},
}


# ============================================================
# ESTADO
# ============================================================
def portafolio_nuevo():
    return {
        "version": VERSION,
        "estrategias": {n: {"capital": CAPITAL_INICIAL, "posiciones": {},
                            "pendientes": [], "historial": []}
                        for n in ESTRATEGIAS},
        "ultima_fecha": {},
        "bh_ref": {},
        "inicio": None
    }


def cargar_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            data = json.load(f)
        if data.get("version") == VERSION:
            return data
        print("Formato anterior detectado: se reinicia el experimento.")
    return portafolio_nuevo()


def guardar_portfolio(port):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(port, f, indent=2)


# ============================================================
# MECANICA DE TRADING (identica para todas las estrategias)
# ============================================================
def cerrar(p, nombre, ticker, precio_bruto, motivo, fecha, eventos):
    pos = p["posiciones"].pop(ticker)
    salida = precio_bruto * (1 - SLIPPAGE)
    pnl = pos["unidades"] * (salida - pos["entry"]) \
        - pos["unidades"] * salida * COMISION
    p["capital"] += pos["unidades"] * pos["entry"] + pnl
    p["historial"].append({"ticker": ticker, "entrada": pos["entry"],
                           "salida": round(salida, 2), "pnl": round(pnl, 2),
                           "motivo": motivo, "fecha_in": pos["fecha"],
                           "fecha_out": fecha})
    emoji = "✅" if pnl > 0 else "❌"
    eventos.append(f"{emoji} [{nombre}] {ticker} cerrada ({motivo}): ${pnl:+.2f}")


def gestionar_estrategia(nombre, est, p, ticker, barra, ctx, eventos):
    open_p, high_p, low_p, close_p = barra["ohlc"]
    fecha, atr, atr_prev, prev = barra["fecha"], barra["atr"], barra["atr_prev"], barra["prev"]

    # 1. gestion de posicion abierta
    if ticker in p["posiciones"]:
        pos = p["posiciones"][ticker]
        if open_p <= pos["stop"]:
            cerrar(p, nombre, ticker, open_p, "STOP (gap)", fecha, eventos)
        elif open_p >= pos["tp"]:
            cerrar(p, nombre, ticker, open_p, "TP (gap)", fecha, eventos)
        elif est["salida"] and est["salida"](prev):
            cerrar(p, nombre, ticker, open_p, "SALIDA RSI", fecha, eventos)
        elif low_p <= pos["stop"]:
            cerrar(p, nombre, ticker, pos["stop"], "STOP", fecha, eventos)
        elif high_p >= pos["tp"]:
            cerrar(p, nombre, ticker, pos["tp"], "TAKE PROFIT", fecha, eventos)
        else:
            nuevo = round(close_p - 1.5 * atr, 2)
            if nuevo > pos["stop"]:
                pos["stop"] = nuevo

    # 2. ejecutar entrada pendiente al Open de hoy
    if ticker in p["pendientes"]:
        p["pendientes"].remove(ticker)
        if ticker not in p["posiciones"]:
            dist = 2 * atr_prev
            fill = open_p * (1 + SLIPPAGE)
            if dist > 0 and fill > 0:
                unidades = round(min(p["capital"] * RIESGO / dist,
                                     p["capital"] / fill), 6)
                if unidades * fill >= 1:
                    costo = unidades * fill
                    p["capital"] -= costo + costo * COMISION
                    p["posiciones"][ticker] = {
                        "unidades": unidades, "entry": round(fill, 2),
                        "stop": round(fill - dist, 2),
                        "tp": round(fill + 2 * dist, 2), "fecha": fecha}
                    eventos.append(f"🟢 [{nombre}] {ticker} compra: "
                                   f"{unidades} und @ ${fill:.2f}")
                    pos = p["posiciones"][ticker]
                    if low_p <= pos["stop"]:
                        cerrar(p, nombre, ticker, pos["stop"], "STOP (mismo día)",
                               fecha, eventos)
                    elif high_p >= pos["tp"]:
                        cerrar(p, nombre, ticker, pos["tp"], "TP (mismo día)",
                               fecha, eventos)

    # 3. nueva senal -> entra manana al Open
    if ticker not in p["posiciones"] and ticker not in p["pendientes"]:
        if est["senal"](barra["u"], ctx):
            p["pendientes"].append(ticker)
            eventos.append(f"📌 [{nombre}] {ticker}: señal, entra mañana")


def procesar_ticker(ticker, port, eventos):
    df = obtener_datos(ticker)
    if len(df) < 120:
        return None
    df = calcular_indicadores(df)
    u, prev = df.iloc[-1], df.iloc[-2]
    fecha = str(u.name.date())
    close_p = float(u["Close"])

    port["bh_ref"].setdefault(ticker, close_p)
    if port["ultima_fecha"].get(ticker) == fecha:
        return close_p
    port["ultima_fecha"][ticker] = fecha

    # contexto compartido (se calcula UNA vez por ticker)
    breakout = close_p > float(df["High"].rolling(20).max().shift(1).iloc[-1])
    ia_prob = None
    try:
        modelo, _, _ = entrenar_IA(df)
        ia_prob = float(modelo.predict_proba(df[FEATURES].iloc[[-1]])[0, 1])
    except Exception as e:
        eventos.append(f"⚠ IA {ticker}: {e}")
    sentimiento = analizar_sentimiento(ticker)

    ctx = {"breakout": breakout, "ia_prob": ia_prob, "sentimiento": sentimiento}
    barra = {"ohlc": (float(u["Open"]), float(u["High"]),
                      float(u["Low"]), close_p),
             "fecha": fecha, "atr": float(u["ATR"]),
             "atr_prev": float(prev["ATR"]), "u": u, "prev": prev}

    for nombre, est in ESTRATEGIAS.items():
        gestionar_estrategia(nombre, est, port["estrategias"][nombre],
                             ticker, barra, ctx, eventos)
    return close_p


# ============================================================
# EJECUCION
# ============================================================
if __name__ == "__main__":
    port = cargar_portfolio()
    eventos = []
    cierres = {}

    for t in TICKERS:
        try:
            c = procesar_ticker(t, port, eventos)
            if c is not None:
                cierres[t] = c
        except Exception as e:
            eventos.append(f"⚠ {t}: error ({e})")

    hoy = max(port["ultima_fecha"].values()) if port["ultima_fecha"] else "N/D"
    if port["inicio"] is None:
        port["inicio"] = hoy

    bh = [cierres[tk] / port["bh_ref"][tk] - 1
          for tk in cierres if tk in port["bh_ref"]]
    bh_ret = (sum(bh) / len(bh) * 100) if bh else 0.0

    # tabla comparativa
    filas = []
    for nombre, p in port["estrategias"].items():
        p["pendientes"] = [t for t in p["pendientes"] if t in cierres]
        equity = p["capital"] + sum(pos["unidades"] * cierres.get(tk, pos["entry"])
                                    for tk, pos in p["posiciones"].items())
        ret = (equity / CAPITAL_INICIAL - 1) * 100
        filas.append((nombre, equity, ret, len(p["posiciones"]),
                      len(p["historial"])))
    filas.sort(key=lambda x: -x[1])

    medallas = ["🥇", "🥈", "🥉"]
    lineas = [f"🧪 <b>PAPER TRADING</b> ({hoy})",
              f"Competencia de estrategias — ${CAPITAL_INICIAL} c/u",
              ""]
    for i, (nombre, eq, ret, npos, ntr) in enumerate(filas):
        m = medallas[i] if i < len(medallas) else "•"
        lineas.append(f"{m} <b>{nombre}</b>: ${eq:.2f} ({ret:+.2f}%) | "
                      f"{npos} pos | {ntr} trades")
    lineas.append(f"📈 Buy & Hold: {bh_ret:+.2f}%")

    if eventos:
        lineas.append("\n<b>Hoy:</b>")
        lineas += [f"  {e}" for e in eventos]
    else:
        lineas.append("\nSin movimientos hoy.")

    lineas.append("\n<i>Dinero ficticio. Solo validación del plan.</i>")
    mensaje = "\n".join(lineas)

    guardar_portfolio(port)
    print(mensaje)
    ok = enviar_telegram(mensaje)
    print("\nTelegram:", "enviado ✓" if ok else "NO enviado")
