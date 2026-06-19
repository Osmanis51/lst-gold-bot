# ════════════════════════════════════════════════════
#  LST Gold Bot — Docker Image
#  Liquidity Side Theory | XAUUSD → Telegram 24/7
# ════════════════════════════════════════════════════

FROM python:3.11-slim

# Metadatos
LABEL maintainer="LST Gold Bot"
LABEL description="Liquidity Side Theory Bot — XAUUSD signals to Telegram"

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Timezone UTC
ENV TZ=UTC

# Copiar requirements e instalar librerías Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el bot
COPY lst_bot.py .

# Variables de entorno (se sobreescriben al desplegar)
ENV TG_TOKEN=""
ENV TG_CHAT_ID=""
ENV BALANCE="10000"
ENV RISK_PCT="0.50"
ENV SL_PIPS="150"

# Comando de inicio
CMD ["python", "-u", "lst_bot.py"]
