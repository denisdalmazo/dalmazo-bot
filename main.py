"""
DalmazoAdv WhatsApp Bot
Bot de atendimento e qualificação de leads para escritório de advocacia.
Stack: FastAPI + Evolution API + OpenAI + Supabase
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
from openai import OpenAI
from supabase import create_client, Client

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Configurações via variáveis de ambiente ─────────────────────────────────
EVOLUTION_API_URL   = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY   = os.getenv("EVOLUTION_API_KEY", "")
INSTANCE_NAME       = os.getenv("INSTANCE_NAME", "dalmazo")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
SUPABASE_URL        = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY        = os.getenv("SUPABASE_KEY", "")
OPENAI_MODEL        = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# ─── Clientes ────────────────────────────────────────────────────────────────
openai_client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

app = FastAPI(title="DalmazoAdv WhatsApp Bot", version="1.0.0")

# ─── Memória de conversas (em memória; para produção use Redis/Supabase) ─────
conversation_history: dict[str, list] = {}

# ─── Prompt do sistema ────────────────────────────────────────────────────────
SYSTEM_PROMPT = """Você é o assistente virtual do escritório **Dalmazo & Co.**, um escritório de advocacia especializado em atendimento jurídico de qualidade.

Seu papel é:
1. **Recepcionar** o cliente com cordialidade e profissionalismo
2. **Entender o problema jurídico** do cliente fazendo perguntas estratégicas
3. **Qualificar o lead** coletando as informações abaixo de forma natural (não como formulário):
   - Nome completo
   - Telefone (já temos pelo WhatsApp)
   - Área jurídica do problema (trabalhista, família, criminal, cível, previdenciário, consumidor, imobiliário, empresarial, outro)
   - Descrição resumida do caso
   - Urgência (urgente / normal / pode aguardar)
4. **Calcular um score de interesse** internamente (0-100) com base em:
   - Clareza do problema (0-30 pts)
   - Urgência (0-30 pts)
   - Viabilidade jurídica aparente (0-40 pts)
5. **Agendar** ou sugerir uma consulta com o Dr. Denis Dalmazo

Regras importantes:
- NUNCA dê pareceres jurídicos definitivos — apenas oriente que o advogado irá analisar
- Seja empático, especialmente em casos de família, criminal e trabalhista
- Use linguagem acessível, sem jargões excessivos
- Quando tiver nome, área e descrição do caso, informe que irá registrar o contato e que o advogado entrará em contato em breve
- Mantenha respostas curtas e objetivas (máximo 3 parágrafos)
- Responda SEMPRE em português brasileiro

Quando coletar todas as informações necessárias, finalize com:
"✅ Perfeito, [NOME]! Registrei seu contato. O Dr. Denis Dalmazo entrará em contato em breve para agendar sua consulta. Caso precise de algo urgente, pode ligar diretamente. Tenha um ótimo dia! 🙏"
"""

# ─── Funções auxiliares ───────────────────────────────────────────────────────

def get_or_create_conversation(phone: str) -> list:
    """Retorna o histórico de conversa ou cria um novo."""
    if phone not in conversation_history:
        conversation_history[phone] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
    return conversation_history[phone]


def extract_lead_data(phone: str, messages: list) -> dict:
    """
    Usa a IA para extrair dados estruturados do lead a partir da conversa.
    Retorna um dicionário com os campos do lead.
    """
    extraction_prompt = """Com base na conversa abaixo, extraia as informações do lead em formato JSON.
Retorne APENAS o JSON, sem explicações.

Campos a extrair:
{
  "nome": "nome completo ou null",
  "telefone": "telefone informado ou null",
  "area_juridica": "trabalhista|familia|criminal|civel|previdenciario|consumidor|imobiliario|empresarial|outro|null",
  "descricao_caso": "resumo do caso em 1-2 frases ou null",
  "urgencia": "urgente|normal|pode_aguardar|null",
  "score": número de 0 a 100,
  "status": "novo|qualificado|agendado"
}

Conversa:
"""
    
    # Monta a conversa para extração (sem o system prompt)
    conv_text = "\n".join([
        f"{'Cliente' if m['role'] == 'user' else 'Bot'}: {m['content']}"
        for m in messages if m['role'] != 'system'
    ])
    
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "user", "content": extraction_prompt + conv_text}
            ],
            temperature=0,
            max_tokens=300
        )
        raw = response.choices[0].message.content.strip()
        # Remove possíveis blocos de código markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Erro ao extrair dados do lead: {e}")
        return {}


def save_lead_to_supabase(phone: str, lead_data: dict) -> bool:
    """Salva ou atualiza o lead no Supabase."""
    if not supabase:
        logger.warning("Supabase não configurado. Lead não salvo.")
        return False
    
    try:
        # Verifica se o lead já existe pelo telefone
        existing = supabase.table("leads").select("id").eq("telefone", phone).execute()
        
        payload = {
            "telefone": phone,
            "nome": lead_data.get("nome"),
            "area_juridica": lead_data.get("area_juridica"),
            "descricao_caso": lead_data.get("descricao_caso"),
            "urgencia": lead_data.get("urgencia"),
            "score": lead_data.get("score", 0),
            "status": lead_data.get("status", "novo"),
            "origem": "whatsapp_bot",
            "updated_at": datetime.utcnow().isoformat()
        }
        
        if existing.data:
            # Atualiza lead existente
            supabase.table("leads").update(payload).eq("telefone", phone).execute()
            logger.info(f"Lead atualizado: {phone}")
        else:
            # Cria novo lead
            payload["created_at"] = datetime.utcnow().isoformat()
            supabase.table("leads").insert(payload).execute()
            logger.info(f"Novo lead criado: {phone}")
        
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar lead no Supabase: {e}")
        return False


async def send_whatsapp_message(phone: str, message: str) -> bool:
    """Envia mensagem via Evolution API."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": phone,
        "text": message
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Mensagem enviada para {phone}")
            return True
    except Exception as e:
        logger.error(f"Erro ao enviar mensagem para {phone}: {e}")
        return False


def generate_ai_response(phone: str, user_message: str) -> str:
    """Gera resposta da IA com base no histórico da conversa."""
    history = get_or_create_conversation(phone)
    history.append({"role": "user", "content": user_message})
    
    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=500
        )
        ai_reply = response.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": ai_reply})
        
        # Mantém histórico com no máximo 20 mensagens (+ system prompt)
        if len(history) > 21:
            conversation_history[phone] = [history[0]] + history[-20:]
        
        return ai_reply
    except Exception as e:
        logger.error(f"Erro ao gerar resposta da IA: {e}")
        return "Desculpe, estou com uma instabilidade momentânea. Por favor, tente novamente em alguns instantes. 🙏"


def should_save_lead(messages: list) -> bool:
    """Verifica se há informações suficientes para salvar o lead."""
    # Salva após pelo menos 3 trocas de mensagens (6 mensagens sem o system)
    user_messages = [m for m in messages if m['role'] == 'user']
    return len(user_messages) >= 2


# ─── Rotas ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "online", "bot": "DalmazoAdv WhatsApp Bot", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "supabase": "connected" if supabase else "not configured",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Recebe eventos da Evolution API."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    event = body.get("event", "")
    logger.info(f"Evento recebido: {event}")
    
    # Processa apenas mensagens recebidas
    if event != "messages.upsert":
        return JSONResponse({"status": "ignored", "event": event})
    
    data = body.get("data", {})
    
    # Ignora mensagens enviadas pelo próprio bot
    if data.get("key", {}).get("fromMe", False):
        return JSONResponse({"status": "ignored", "reason": "own message"})
    
    # Extrai informações da mensagem
    message_obj = data.get("message", {})
    
    # Suporte a texto simples e mensagens de lista/botão
    user_message = (
        message_obj.get("conversation") or
        message_obj.get("extendedTextMessage", {}).get("text") or
        message_obj.get("buttonsResponseMessage", {}).get("selectedDisplayText") or
        ""
    ).strip()
    
    if not user_message:
        logger.info("Mensagem sem texto, ignorando.")
        return JSONResponse({"status": "ignored", "reason": "no text"})
    
    # Extrai número do remetente
    remote_jid = data.get("key", {}).get("remoteJid", "")
    phone = remote_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")
    
    if not phone:
        return JSONResponse({"status": "error", "reason": "no phone"})
    
    # Ignora mensagens de grupos
    if "@g.us" in remote_jid:
        return JSONResponse({"status": "ignored", "reason": "group message"})
    
    logger.info(f"Mensagem de {phone}: {user_message[:50]}...")
    
    # Gera resposta da IA
    ai_reply = generate_ai_response(phone, user_message)
    
    # Envia resposta via WhatsApp
    await send_whatsapp_message(phone, ai_reply)
    
    # Salva/atualiza lead no Supabase (em background)
    history = get_or_create_conversation(phone)
    if should_save_lead(history):
        lead_data = extract_lead_data(phone, history)
        if lead_data:
            save_lead_to_supabase(phone, lead_data)
    
    return JSONResponse({"status": "ok", "phone": phone})


@app.delete("/conversation/{phone}")
async def clear_conversation(phone: str):
    """Limpa o histórico de conversa de um número (útil para testes)."""
    if phone in conversation_history:
        del conversation_history[phone]
        return {"status": "cleared", "phone": phone}
    return {"status": "not_found", "phone": phone}


@app.get("/leads/summary")
async def leads_summary():
    """Retorna um resumo dos leads ativos em memória."""
    summary = []
    for phone, history in conversation_history.items():
        user_msgs = [m for m in history if m['role'] == 'user']
        summary.append({
            "phone": phone,
            "messages": len(user_msgs),
            "last_message": user_msgs[-1]['content'][:50] if user_msgs else ""
        })
    return {"total": len(summary), "leads": summary}
