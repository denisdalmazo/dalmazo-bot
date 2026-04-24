import os
import json
import logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import httpx
import google.generativeai as genai

logging.basicConfig(level=logging.INFO )
logger = logging.getLogger(__name__)

# Configurações
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080" )
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "dalmazo")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# IA
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')

app = FastAPI()
conversation_history = {}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"Recebido: {body.get('event')}")
    
    if body.get("event") == "messages.upsert":
        data = body.get("data", {})
        if not data.get("key", {}).get("fromMe"):
            phone = data.get("key", {}).get("remoteJid", "").split("@")[0]
            user_msg = data.get("message", {}).get("conversation") or data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
            
            if user_msg:
                # Resposta da IA
                response = model.generate_content(f"Você é assistente do escritório Dalmazo & Co. Responda: {user_msg}")
                ai_reply = response.text
                
                # Enviar para WhatsApp
                url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
                headers = {"apikey": EVOLUTION_API_KEY}
                async with httpx.AsyncClient( ) as client:
                    await client.post(url, json={"number": phone, "text": ai_reply}, headers=headers)
    
    return {"status": "ok"}

@app.get("/")
async def root(): return {"message": "Bot Online"}
