cat <<'EOF' > ~/dalmazo-whatsapp-bot/main.py
import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import google.generativeai as genai
from supabase import create_client, Client

logging.basicConfig(level=logging.INFO, format="%(asctime )s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080" )
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "dalmazo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI(title="DalmazoAdv WhatsApp Bot")
conversation_history = {}

SYSTEM_PROMPT = """Você é o assistente virtual do escritório Dalmazo & Co. 
Seu papel é recepcionar clientes, entender o problema jurídico e qualificar o lead coletando: 
Nome, Área jurídica e Descrição do caso. Seja empático e profissional. 
NUNCA dê pareceres jurídicos definitivos. Finalize dizendo que o Dr. Denis entrará em contato."""

def get_chat(phone):
    if phone not in conversation_history:
        conversation_history[phone] = model.start_chat(history=[])
        conversation_history[phone].send_message(SYSTEM_PROMPT)
    return conversation_history[phone]

async def send_whatsapp(phone, text):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    async with httpx.AsyncClient( ) as client:
        await client.post(url, json={"number": phone, "text": text}, headers=headers)

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    if body.get("event") != "messages.upsert" or body.get("data", {}).get("key", {}).get("fromMe"):
        return {"status": "ignored"}
    
    data = body.get("data", {})
    phone = data.get("key", {}).get("remoteJid", "").split("@")[0]
    user_msg = data.get("message", {}).get("conversation") or data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
    
    if not user_msg or "@g.us" in data.get("key", {}).get("remoteJid", ""):
        return {"status": "ignored"}

    chat = get_chat(phone)
    response = chat.send_message(user_msg)
    await send_whatsapp(phone, response.text)
    return {"status": "ok"}

@app.get("/health")
async def health(): return {"status": "online"}
EOF
