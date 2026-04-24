import os
import json
import logging
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
import google.generativeai as genai
from supabase import create_client, Client

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime )s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Variáveis de Ambiente (Configuradas no EasyPanel)
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080" )
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "dalmazo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Inicialização do Gemini (Grátis)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

# Inicialização do Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI(title="DalmazoAdv WhatsApp Bot")
conversation_history = {}

SYSTEM_PROMPT = """Você é o assistente virtual do escritório Dalmazo & Co. 
Seu papel é recepcionar clientes, entender o problema jurídico e qualificar o lead coletando: 
Nome, Área jurídica e Descrição do caso. Seja empático e profissional. 
NUNCA dê pareceres jurídicos definitivos. Finalize dizendo que o Dr. Denis entrará em contato."""

def get_chat(phone):
    if phone not in conversation_history:
        # Inicia uma nova conversa com o histórico vazio e o prompt do sistema
        chat = model.start_chat(history=[])
        chat.send_message(SYSTEM_PROMPT)
        conversation_history[phone] = chat
    return conversation_history[phone]

async def send_whatsapp(phone, text):
    # Envia a resposta de volta para o WhatsApp via Evolution API
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": phone, "text": text}
    async with httpx.AsyncClient( ) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Mensagem enviada para {phone}")
        except Exception as e:
            logger.error(f"Erro ao enviar WhatsApp: {e}")

@app.post("/webhook")
async def webhook(request: Request):
    try:
        body = await request.json()
    except:
        return {"status": "error", "message": "Invalid JSON"}

    # Processa apenas mensagens recebidas que não sejam do próprio bot
    if body.get("event") != "messages.upsert" or body.get("data", {}).get("key", {}).get("fromMe"):
        return {"status": "ignored"}
    
    data = body.get("data", {})
    remote_jid = data.get("key", {}).get("remoteJid", "")
    phone = remote_jid.split("@")[0]
    
    # Ignora grupos
    if "@g.us" in remote_jid:
        return {"status": "ignored", "reason": "group"}

    user_msg = data.get("message", {}).get("conversation") or \
               data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
    
    if not user_msg:
        return {"status": "ignored", "reason": "no text"}

    logger.info(f"Mensagem de {phone}: {user_msg}")

    # Gera resposta com o Gemini
    try:
        chat = get_chat(phone)
        response = chat.send_message(user_msg)
        ai_reply = response.text
        
        # Envia para o WhatsApp
        await send_whatsapp(phone, ai_reply)
    except Exception as e:
        logger.error(f"Erro no Gemini: {e}")
        await send_whatsapp(phone, "Desculpe, tive um problema técnico. Tente novamente em instantes.")

    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "online", "provider": "gemini"}

@app.get("/")
async def root():
    return {"message": "DalmazoAdv Bot is Running"}
