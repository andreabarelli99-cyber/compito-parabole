import os, json, uuid, datetime, threading, unicodedata
from flask import Flask, jsonify, request, send_from_directory
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

app = Flask(__name__, static_folder='.')
with open('questions.json', encoding='utf-8') as f: QUESTIONS = json.load(f)
lock = threading.Lock()
HEADERS_SUMMARY = ['Timestamp','Email account','Nome e cognome','Classe','Punteggio automatico','Punti massimi automatici','Risposte compilate','Totale domande','Tempo dichiarato (min)','Identificativo']
HEADERS_ANSWERS = ['Identificativo','Timestamp','Email account','Nome e cognome','Classe','Numero','Domanda','Tipo','Risposta studente','Risposta corretta','Esito automatico','Punti','Motivazione / procedimento']

def sheets():
    sheet_id=os.environ.get('GOOGLE_SHEET_ID','').strip()
    raw=os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON','')
    if not sheet_id or not raw: raise RuntimeError('Configurazione Google Fogli incompleta sul server')
    info=json.loads(raw)
    creds=service_account.Credentials.from_service_account_info(info,scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return sheet_id, AuthorizedSession(creds)

def get_values(sid,sess,rng):
    res=sess.get(f'https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}',timeout=20)
    res.raise_for_status(); return res.json().get('values',[])

def append(sid,sess,rng,rows):
    res=sess.post(f'https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{rng}:append',params={'valueInputOption':'RAW','insertDataOption':'INSERT_ROWS'},json={'values':rows},timeout=30)
    res.raise_for_status()

def normalize(x): return ' '.join(str(x or '').strip().casefold().split()).replace(',','.')

def grade(q,value):
    if q['type']=='open': return None
    if q['type']=='multi': return isinstance(value,list) and sorted(value)==sorted(q['correct'])
    return normalize(value)==normalize(q['correct'])

@app.get('/')
def index(): return send_from_directory('.', 'index.html')

@app.get('/api/quiz')
def quiz():
    return jsonify({'title':'Compito – Equazioni di secondo grado, parabole e disequazioni','questions':[{k:v for k,v in q.items() if k not in ('correct','explanation')} for q in QUESTIONS]})

@app.post('/api/submit')
def submit():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): return jsonify(error='Dati non validi'),400
    name=str(data.get('name') or '').strip()[:120]
    cls=str(data.get('className') or '').strip()[:60]
    answers=data.get('answers')
    if not name or not cls or not isinstance(answers,list): return jsonify(error='Inserisci nome, cognome e classe'),400
    if len(answers)>len(QUESTIONS): return jsonify(error='Numero di risposte non valido'),400
    answer_map={a.get('id'):a for a in answers if isinstance(a,dict) and isinstance(a.get('id'),str)}
    now=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    receipt=str(uuid.uuid4()); rows=[]; score=max_points=filled=0
    for i,q in enumerate(QUESTIONS,1):
        a=answer_map.get(q['id'],{});value=a.get('value','');reason=str(a.get('reason') or '')[:4000]
        if q['type']=='multi': value=value if isinstance(value,list) else []
        else: value=str(value or '')[:2000]
        if value: filled+=1
        correct=grade(q,value)
        points=int(q.get('points',0)) if correct else 0
        if q['type']!='open': max_points+=int(q.get('points',0))
        score+=points
        rows.append([receipt,now,'',name,cls,i,q['prompt'],q['type'],' | '.join(value) if isinstance(value,list) else value,' | '.join(q['correct']) if isinstance(q.get('correct'),list) else q.get('correct',''),'Da correggere' if correct is None else 'Corretta' if correct else 'Errata',points,reason])
    try: duration=max(0,min(9999,int(float(data.get('durationMinutes') or 0))))
    except (ValueError,TypeError): duration=0
    try:
        with lock:
            sid,sess=sheets()
            previous=get_values(sid,sess,"'Consegne'!C2:D")
            if any(len(r)>1 and normalize(r[0])==normalize(name) and normalize(r[1])==normalize(cls) for r in previous):
                return jsonify(error='Risulta già una consegna con questo nome e questa classe. Contatta il docente.'),409
            # Write detailed answers first, summary last. The summary is the confirmation record.
            append(sid,sess,"'Risposte'!A:M",rows)
            append(sid,sess,"'Consegne'!A:J",[[now,'',name,cls,score,max_points,filled,len(QUESTIONS),duration,receipt]])
    except Exception:
        app.logger.exception('Errore registrazione')
        return jsonify(error='Impossibile registrare la consegna. Avvisa il docente e conserva le risposte.'),503
    return jsonify(ok=True,id=receipt,filled=filled,total=len(QUESTIONS))
