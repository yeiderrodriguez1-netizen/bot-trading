# Guía: Bot de paper trading con reporte a tu Android por Telegram

El bot corre gratis en la nube (GitHub Actions) y opera un **portafolio ficticio de $200** para validar el plan sin arriesgar dinero real. Cada día te llega a Telegram el estado: equity, posiciones abiertas, operaciones del día y comparación contra buy & hold. Tu teléfono solo recibe el mensaje — no necesita estar encendido cuando corre el bot.

## Paso 1: Crear tu bot de Telegram (5 min, desde el teléfono)

1. Abre Telegram y busca **@BotFather** (verificado, con check azul).
2. Escríbele `/newbot`. Te pedirá un nombre (ej. "Mis Señales Trading") y un usuario que termine en `bot` (ej. `yeyo_senales_bot`).
3. BotFather te dará un **token** tipo `123456789:AAH6fk...`. **Guárdalo, es tu TELEGRAM_TOKEN.** No lo compartas con nadie.
4. Búscale el chat a tu nuevo bot y envíale cualquier mensaje (ej. "hola") para activarlo.
5. Para obtener tu **chat_id**: abre en el navegador (reemplaza TU_TOKEN):
   `https://api.telegram.org/botTU_TOKEN/getUpdates`
   Busca `"chat":{"id":123456789...` — ese número es tu **TELEGRAM_CHAT_ID**.

## Paso 2: Crear el repositorio en GitHub (10 min)

1. Crea una cuenta en [github.com](https://github.com) si no tienes.
2. Crea un repositorio nuevo, **privado**, ej. `bot-trading`.
3. Sube estos archivos (botón "Add file" → "Upload files" o "Create new file"):
   - `bot_telegram.py` (en la raíz — contiene las funciones de análisis)
   - `bot_paper_trading.py` (en la raíz — el simulador que se ejecuta a diario)
   - `requirements.txt` (en la raíz)
   - `senal_diaria.yml` → este va en la ruta `.github/workflows/senal_diaria.yml`
     (al crear el archivo escribe la ruta completa en el nombre: `.github/workflows/senal_diaria.yml`)

## Paso 3: Configurar los secretos (5 min)

1. En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**.
2. Crea dos secretos:
   - Nombre: `TELEGRAM_TOKEN` → valor: el token de BotFather
   - Nombre: `TELEGRAM_CHAT_ID` → valor: tu chat_id

## Paso 4: Probar

1. Ve a la pestaña **Actions** de tu repositorio.
2. Selecciona "Paper trading diario" → **Run workflow** → Run.
3. En 2-3 minutos debe llegarte el mensaje a Telegram. Si falla, abre la ejecución y revisa el log del paso "Ejecutar paper trading".
4. Tras la primera ejecución aparecerá un archivo `portfolio.json` en tu repo: ahí vive el portafolio ficticio (capital, posiciones, historial). No lo edites a mano.

## ¿Cómo funciona el portafolio ficticio?

- Arranca con $200 ficticios (el monto que definiste como máximo por ahora) y arriesga 2% por operación (~$4), con comisión (0.1%) y slippage (0.05%) simulados para que el resultado sea realista.
- Usa unidades fraccionarias (con $200 no alcanza para 1 acción entera de AAPL). Si algún día pasas a dinero real, tu broker debe permitir fracciones.
- Para cambiar el monto, edita `CAPITAL_INICIAL` en `bot_paper_trading.py` y borra `portfolio.json` para reiniciar.
- Cuando hay señal, la compra se ejecuta al precio de apertura del día siguiente (como pasaría en la realidad).
- Cada posición tiene stop loss, take profit y trailing stop gestionados automáticamente.
- El reporte diario compara tu estrategia contra buy & hold: **si tras 2-3 meses no lo supera, el plan no está funcionando** — esa es la validación.
- Para reiniciar el experimento desde cero: borra `portfolio.json` del repo.

## Horarios (ya configurados en senal_diaria.yml)

- Lunes a viernes 4:35 PM Colombia: tras el cierre de la bolsa de NY (acciones + BTC).
- Sábado y domingo 7:00 AM Colombia: solo relevante para BTC-USD.

Para cambiarlos, edita las líneas `cron` (están en hora UTC; Colombia = UTC-5). Ojo: GitHub Actions puede retrasar la ejecución 5-15 min, es normal.

## Preguntas frecuentes

**¿Por qué no corre 24/7?** El bot analiza velas diarias: solo hay información nueva una vez al día. Correrlo cada minuto no genera más señales.

**¿Cuánto cuesta?** Nada. GitHub Actions da 2,000 minutos/mes gratis en repos privados; el bot usa ~3 min/día ≈ 90 min/mes.

**¿Puedo cambiar los tickers o el capital?** Sí, edita `TICKERS`, `CAPITAL` y `RIESGO` al inicio de `bot_telegram.py` directamente en GitHub.

**¿El bot compra por mí?** No. Solo envía señales informativas; tú decides si operar en tu broker. Esto es deliberado: conectarlo a un broker real requiere pruebas mucho más serias.

## Advertencia

Las señales son informativas, no asesoría financiera. La "precisión" que muestra el mensaje es una validación limitada; antes de arriesgar dinero real, lleva un registro de las señales durante 2-3 meses (paper trading) y compara contra simplemente comprar y mantener.
