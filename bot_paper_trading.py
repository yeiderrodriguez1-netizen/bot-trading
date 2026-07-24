# ============================================================
# PAPER TRADING: portafolio ficticio persistente
# - Usa las senales del bot (tecnica + IA + sentimiento)
# - Entra al Open del dia siguiente a la senal (sin lookahead)
# - Gestiona stop, take profit y trailing stop
# - Guarda el estado en portfolio.json (se commitea al repo)
# - Reporta por Telegram: equity, posiciones, trades y vs Buy&Hold
# ============================================================
import os
import json
from bot_telegram import (obtener_datos, calcular_indicadores, entrenar_IA,
                          analizar_sentimiento, enviar_telegram,
                          FEATURES, UMBRAL_IA, UMBRAL_SENT, TICKERS, RIESGO)

PORTFOLIO_FILE = "portfolio.json"
CAPITAL_INICIAL = 200   # capital ficticio maximo a "invertir" por ahora
COMISION = 0.001        # 0.1% por lado
SLIPPAGE = 0.0005       # 0.05% por lado
# Con $200 se usan unidades FRACCIONARIAS en todos los tickers
# (1 accion de AAPL ya cuesta mas que el capital total).
# Si pasas a dinero real, tu broker debe permitir fracciones.


# ============================================================
# ESTADO
# ============================================================
def cargar_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {
        "capital": CAPITAL_INICIAL,      # dinero liquido ficticio
        "posiciones": {},                # {ticker: {unidades, entry, stop, tp, fecha}}
        "pendientes": [],                # senales que entran al proximo Open
        "historial": [],                 # trades cerrados
        "equity_hist": [],               # [{fecha, equity}]
        "ultima_fecha": {},              # ultima vela procesada por ticker
        "bh_ref": {},                    # precio inicial para comparar Buy & Hold
        "inicio": None
    }


def guardar_portfolio(port):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(port, f, indent=2)


# ============================================================
# LOGICA POR TICKER
# ============================================================
def procesar_ticker(ticker, port, eventos):
    """Gestiona posicion abierta, ejecuta entradas pendientes y
    genera nuevas senales. Devuelve el Close para valorar equity."""
    df = obtener_datos(ticker)
    if len(df) < 120:
        return None
    df = calcular_indicadores(df)
    u = df.iloc[-1]
    fecha = str(u.name.date())
    open_p, high_p = float(u["Open"]), float(u["High"])
    low_p, close_p = float(u["Low"]), float(u["Close"])
    atr = float(u["ATR"])

    port["bh_ref"].setdefault(ticker, close_p)

    # vela ya procesada (fin de semana / re-ejecucion) -> solo valorar
    if port["ultima_fecha"].get(ticker) == fecha:
        return close_p
    port["ultima_fecha"][ticker] = fecha

    def cerrar(precio_bruto, motivo):
        pos = port["posiciones"].pop(ticker)
        salida = precio_bruto * (1 - SLIPPAGE)
        pnl = pos["unidades"] * (salida - pos["entry"]) \
            - pos["unidades"] * salida * COMISION
        port["capital"] += pos["unidades"] * pos["entry"] + pnl
        port["historial"].append({
            "ticker": ticker, "entrada": pos["entry"],
            "salida": round(salida, 2), "unidades": pos["unidades"],
            "pnl": round(pnl, 2), "motivo": motivo,
            "fecha_in": pos["fecha"], "fecha_out": fecha
        })
        emoji = "✅" if pnl > 0 else "❌"
        eventos.append(f"{emoji} {ticker} cerrada ({motivo}): PnL ${pnl:+.2f}")

    # 1. GESTION DE POSICION ABIERTA (con la vela de hoy)
    if ticker in port["posiciones"]:
        pos = port["posiciones"][ticker]
        if open_p <= pos["stop"]:
            cerrar(open_p, "STOP (gap)")
        elif open_p >= pos["tp"]:
            cerrar(open_p, "TAKE PROFIT (gap)")
        elif low_p <= pos["stop"]:
            cerrar(pos["stop"], "STOP")
        elif high_p >= pos["tp"]:
            cerrar(pos["tp"], "TAKE PROFIT")
        else:
            nuevo = round(close_p - 1.5 * atr, 2)
            if nuevo > pos["stop"]:
                pos["stop"] = nuevo
                eventos.append(f"🔒 {ticker}: trailing stop sube a ${nuevo}")

    # 2. EJECUTAR ENTRADA PENDIENTE (al Open de hoy)
    if ticker in port["pendientes"]:
        port["pendientes"].remove(ticker)
        if ticker not in port["posiciones"]:
            atr_prev = float(df.iloc[-2]["ATR"])
            dist = 2 * atr_prev
            fill = open_p * (1 + SLIPPAGE)
            if dist > 0 and fill > 0:
                unidades = min(port["capital"] * RIESGO / dist,
                               port["capital"] / fill)
                unidades = round(unidades, 6)  # fraccionario en todos los tickers
                if unidades * fill < 1:        # posicion menor a $1: no vale la pena
                    unidades = 0
                if unidades > 0:
                    costo = unidades * fill
                    port["capital"] -= costo + costo * COMISION
                    port["posiciones"][ticker] = {
                        "unidades": unidades, "entry": round(fill, 2),
                        "stop": round(fill - dist, 2),
                        "tp": round(fill + 2 * dist, 2), "fecha": fecha
                    }
                    eventos.append(
                        f"🟢 {ticker} COMPRA simulada: {unidades} und @ ${fill:.2f} "
                        f"| SL ${fill - dist:.2f} | TP ${fill + 2*dist:.2f}")
                    # chequeo intradia del dia de entrada
                    pos = port["posiciones"][ticker]
                    if low_p <= pos["stop"]:
                        cerrar(pos["stop"], "STOP (mismo día)")
                    elif high_p >= pos["tp"]:
                        cerrar(pos["tp"], "TAKE PROFIT (mismo día)")

    # 3. NUEVA SENAL (entra manana al Open)
    if ticker not in port["posiciones"] and ticker not in port["pendientes"]:
        modelo, precision, tasa_base = entrenar_IA(df)
        ia_prob = float(modelo.predict_proba(df[FEATURES].iloc[[-1]])[0, 1])
        tendencia = u["SMA20"] > u["SMA50"]
        breakout = close_p > float(df["High"].rolling(20).max().shift(1).iloc[-1])
        senal = bool(tendencia and breakout and ia_prob > UMBRAL_IA)

        if senal:
            sent = analizar_sentimiento(ticker)
            if sent is not None and sent < UMBRAL_SENT:
                eventos.append(f"🔴 {ticker}: señal bloqueada por sentimiento ({sent:.2f})")
            else:
                port["pendientes"].append(ticker)
                eventos.append(f"📌 {ticker}: nueva señal (IA {ia_prob*100:.0f}%, "
                               f"prec. {precision*100:.0f}% vs base {tasa_base*100:.0f}%). "
                               f"Entra mañana al Open.")

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

    # limpiar pendientes de tickers sin datos
    port["pendientes"] = [t for t in port["pendientes"] if t in cierres]

    # equity = liquido + valor de posiciones abiertas
    valor_pos = sum(p["unidades"] * cierres.get(tk, p["entry"])
                    for tk, p in port["posiciones"].items())
    equity = port["capital"] + valor_pos

    hoy = max(port["ultima_fecha"].values()) if port["ultima_fecha"] else "N/D"
    if port["inicio"] is None:
        port["inicio"] = hoy
    if not port["equity_hist"] or port["equity_hist"][-1]["fecha"] != hoy:
        port["equity_hist"].append({"fecha": hoy, "equity": round(equity, 2)})

    ret_total = (equity / CAPITAL_INICIAL - 1) * 100
    bh = [cierres[tk] / port["bh_ref"][tk] - 1
          for tk in cierres if tk in port["bh_ref"]]
    bh_ret = (sum(bh) / len(bh) * 100) if bh else 0.0

    trades = port["historial"]
    ganados = sum(1 for x in trades if x["pnl"] > 0)
    win_rate = f"{ganados/len(trades)*100:.0f}%" if trades else "N/D"

    # ---- MENSAJE ----
    lineas = [f"🧪 <b>PAPER TRADING</b> ({hoy})",
              "",
              f"💰 Equity: <b>${equity:,.2f}</b> ({ret_total:+.2f}%)",
              f"📈 Buy & Hold mismo periodo: {bh_ret:+.2f}%",
              f"💵 Líquido: ${port['capital']:,.2f} | "
              f"Trades: {len(trades)} | Win rate: {win_rate}"]

    if port["posiciones"]:
        lineas.append("\n<b>Posiciones abiertas:</b>")
        for tk, p in port["posiciones"].items():
            actual = cierres.get(tk, p["entry"])
            flot = p["unidades"] * (actual - p["entry"])
            lineas.append(f"  {tk}: {p['unidades']} und @ ${p['entry']} | "
                          f"PnL ${flot:+.2f} | SL ${p['stop']} | TP ${p['tp']}")
    else:
        lineas.append("\nSin posiciones abiertas.")

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
