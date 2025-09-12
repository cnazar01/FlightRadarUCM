# Source/Jobs/app_main.py
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from .bot import answer  # <-- uses your NLU + fr24_tools

app = FastAPI(title="Flight Q&A")

class Ask(BaseModel):
    question: str

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/ask")
def ask(payload: Ask, tz: Optional[str] = Query(default=None, description="IANA timezone, e.g. America/Tegucigalpa")):
    try:
        return {"answer": answer(payload.question, tz)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- simple chat UI served at "/" ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flight Q&A</title>
  <style>
    :root { --bg:#0f172a; --panel:#111827; --you:#1f2937; --bot:#0b7; --text:#e5e7eb; }
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 Inter,system-ui,Arial}
    header{padding:14px 16px;background:#0b1020;border-bottom:1px solid #1f2937}
    .wrap{max-width:820px;margin:0 auto;padding:16px}
    .chat{display:flex;flex-direction:column;gap:12px;min-height:65vh}
    .msg{padding:12px 14px;border-radius:14px;max-width:85%;white-space:pre-wrap}
    .you{align-self:flex-end;background:var(--you)}
    .bot{align-self:flex-start;background:#0b1f15;border:1px solid #0d4}
    .meta{opacity:.7;font-size:12px;margin-bottom:4px}
    form{display:flex;gap:10px;position:sticky;bottom:0;padding:10px;background:linear-gradient(0deg, var(--bg), rgba(15,23,42,.6))}
    input[type=text]{flex:1;padding:12px;border-radius:10px;border:1px solid #334155;background:#0b1020;color:var(--text)}
    button{padding:12px 16px;border-radius:10px;border:0;background:#10b981;color:#052;cursor:pointer;font-weight:600}
    button[disabled]{opacity:.7;cursor:not-allowed}
  </style>
</head>
<body>
  <header><div class="wrap"><strong>Flight Q&A</strong></div></header>
  <div class="wrap">
    <div id="chat" class="chat"></div>
    <form id="f">
      <input id="q" type="text" placeholder="Try: arrivals at TPA  |  UA2476 arriving at TPA" autocomplete="off" />
      <button id="send" type="submit">Ask</button>
    </form>
  </div>

<script>
const chat = document.getElementById('chat');
const form = document.getElementById('f');
const box  = document.getElementById('q');
const btn  = document.getElementById('send');

function bubble(role, text){
  const el = document.createElement('div');
  el.className = 'msg ' + (role === 'you' ? 'you' : 'bot');
  el.textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = box.value.trim();
  if(!q) return;
  bubble('you', q);
  box.value = '';
  btn.disabled = true;
  try{
    const res = await fetch('/ask', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question: q})
    });
    const data = await res.json();
    bubble('bot', data.answer || 'No answer.');
  }catch(err){
    bubble('bot', 'Error: ' + (err?.message || err));
  }finally{
    btn.disabled = false;
    box.focus();
  }
});
</script>
</body>
</html>
"""
