import os, re, json, sqlite3, secrets, ipaddress, socket
from time import monotonic
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse
import requests
from flask import Flask, request, jsonify, session, send_from_directory

ROOT=os.path.dirname(os.path.abspath(__file__))
DB=os.getenv("DATABASE_PATH",os.path.join(ROOT,"omnia.sqlite3"))
app=Flask(__name__,static_folder="frontend")
app.secret_key=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=True,MAX_CONTENT_LENGTH=2*1024*1024)
MODEL=os.getenv("OPENAI_MODEL","gpt-6.2-astra")
API_KEY=os.getenv("OPENAI_API_KEY","").strip()

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS characters(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,role TEXT,traits TEXT,voice TEXT,background TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS connectors(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,base_url TEXT NOT NULL,auth_env TEXT,status TEXT DEFAULT 'planned',created_at TEXT);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,url TEXT NOT NULL,owner_confirmed INTEGER,blueprint TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS ip_dossiers(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,description TEXT,technical_problem TEXT,solution TEXT,prior_art TEXT,status TEXT DEFAULT 'draft',created_at TEXT);
        CREATE TABLE IF NOT EXISTS inquiries(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT NOT NULL,intent TEXT,asset_type TEXT,title TEXT,amount TEXT,message TEXT,status TEXT DEFAULT 'new',created_at TEXT);
        CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,kind TEXT,description TEXT,valuation REAL,units REAL,investor_share REAL DEFAULT 70,platform_share REAL DEFAULT 30,status TEXT DEFAULT 'demo-only',created_at TEXT);
        """)
init_db()

def admin(fn):
    @wraps(fn)
    def wrapped(*a,**k):
        if not session.get("superadmin"): return jsonify(error="Superadmin-Anmeldung erforderlich."),401
        return fn(*a,**k)
    return wrapped

def public_host(host):
    try:
        if not host or host.lower() in {"localhost","metadata.google.internal"}: return False
        for info in socket.getaddrinfo(host,None):
            ip=ipaddress.ip_address(info[4][0])
            if not ip.is_global: return False
        return True
    except Exception: return False

def safe_public_url(value):
    try:
        p=urlparse(value.strip())
        return p.scheme in ("http","https") and bool(p.hostname) and not p.username and not p.password and public_host(p.hostname)
    except Exception: return False

@app.get("/")
def home(): return send_from_directory("frontend","index.html")

@app.get("/marketplace")
def marketplace_page(): return send_from_directory("frontend","marketplace.html")

@app.get("/health")
def health():
    return jsonify(status="ok",service="AUREALIS OMNIA",openai_configured=bool(API_KEY),superadmin_password_configured=bool(os.getenv("SUPERADMIN_PASSWORD")),database="sqlite-local",live_finance=False)

@app.post("/api/login")
def login():
    expected=os.getenv("SUPERADMIN_PASSWORD","")
    if not expected: return jsonify(error="SUPERADMIN_PASSWORD zuerst in Render Environment setzen."),503
    data=request.get_json(silent=True) or {}
    if not secrets.compare_digest(str(data.get("password","")),expected): return jsonify(error="Anmeldung fehlgeschlagen."),401
    session.clear(); session["superadmin"]=True
    return jsonify(ok=True,role="superadmin")

@app.post("/api/logout")
def logout(): session.clear(); return jsonify(ok=True)

@app.get("/api/status")
def status():
    return jsonify(authenticated=bool(session.get("superadmin")),openai_configured=bool(API_KEY),model=MODEL,web_search="OpenAI Responses API web_search",database="SQLite (lokal; für Produktion PostgreSQL/DATABASE_URL erforderlich)",live_finance_enabled=False)

@app.post("/api/chat")
@admin
def chat():
    if not API_KEY: return jsonify(error="OPENAI_API_KEY fehlt in Render Environment."),503
    data=request.get_json(silent=True) or {}; prompt=str(data.get("message","")).strip()[:12000]
    if not prompt: return jsonify(error="Nachricht fehlt."),400
    persona=data.get("persona") if isinstance(data.get("persona"),dict) else None
    system=("Du bist AUREALIS OMNIA, ein universeller, transparenter Superadmin-Assistent für Recherche, Programmierung, Datenanalyse, "
            "Produktentwicklung, API-Integration, Webarchitektur, IP-Vorprüfung und Finanzmarkt-Compliance. Trenne Fakten, Annahmen und offene Punkte. "
            "Erfinde keine ausgeführten Aktionen, Quellen, API-Zugriffe oder Rechtsfreigaben. Frage vor externen, destruktiven oder kostenpflichtigen Aktionen nach Bestätigung. "
            "Respektiere Rechte an Webseiten, Datenbanken und Inhalten; kein Umgehen von Zugangsschutz. Finanz- und Patentinformationen sind keine Rechtsberatung.")
    if persona:
        system += "\n\nVom Superadmin gewählte fiktionale Charakter-Persona: Name="+str(persona.get("name",""))[:80]+"; Rolle="+str(persona.get("role",""))[:160]+"; Eigenschaften="+str(persona.get("traits",""))[:1000]+"; Sprachstil="+str(persona.get("voice",""))[:500]+"; Hintergrund="+str(persona.get("background",""))[:1200]+". Bleibe in dieser Rolle, ohne Sicherheitsregeln zu überschreiben."
    payload={"model":MODEL,"instructions":system,"input":prompt,"tools":[{"type":"web_search"}],"store":False}
    try:
        r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+API_KEY,"Content-Type":"application/json"},json=payload,timeout=90)
        if not r.ok: app.logger.error("OpenAI API error %s",r.status_code); return jsonify(error="KI-Anfrage fehlgeschlagen. API-Schlüssel, Modellzugriff und Kontingent prüfen."),502
        obj=r.json(); texts=[]; citations=[]
        for item in obj.get("output",[]):
            if item.get("type")=="message":
                for part in item.get("content",[]):
                    if part.get("type")=="output_text":
                        texts.append(part.get("text",""))
                        for ann in part.get("annotations",[]):
                            if ann.get("type")=="url_citation":
                                cite=ann.get("url_citation",ann)
                                url=cite.get("url","")
                                if url.startswith(("https://","http://")):
                                    citations.append({"url":url,"title":cite.get("title") or url})
        return jsonify(answer="\n".join(texts).strip(),citations=citations,model=obj.get("model",MODEL),response_id=obj.get("id"))
    except requests.RequestException: return jsonify(error="Verbindung zum KI-Dienst fehlgeschlagen."),502

@app.route("/api/characters",methods=["GET","POST"])
@admin
def characters():
    if request.method=="GET":
        with db() as c: rows=c.execute("SELECT * FROM characters ORDER BY id DESC").fetchall()
        return jsonify(items=[dict(x) for x in rows])
    d=request.get_json(silent=True) or {}; name=str(d.get("name","")).strip()[:80]
    if not name:return jsonify(error="Name erforderlich."),400
    with db() as c:
        cur=c.execute("INSERT INTO characters(name,role,traits,voice,background,created_at) VALUES(?,?,?,?,?,?)",(name,str(d.get("role",""))[:160],str(d.get("traits",""))[:1200],str(d.get("voice",""))[:500],str(d.get("background",""))[:2000],datetime.now(timezone.utc).isoformat()))
        ident=cur.lastrowid
    return jsonify(id=ident,ok=True)

@app.route("/api/connectors",methods=["GET","POST"])
@admin
def connectors():
    if request.method=="GET":
        with db() as c: rows=c.execute("SELECT * FROM connectors ORDER BY id DESC").fetchall()
        return jsonify(items=[dict(x) for x in rows])
    d=request.get_json(silent=True) or {}; name=str(d.get("name","")).strip()[:100]; url=str(d.get("base_url","")).strip()
    if not name or not safe_public_url(url): return jsonify(error="Name und gültige öffentliche HTTPS/API-URL erforderlich. Lokale und private IP-Adressen sind gesperrt."),400
    with db() as c:
        cur=c.execute("INSERT INTO connectors(name,base_url,auth_env,status,created_at) VALUES(?,?,?,?,?)",(name,url,str(d.get("auth_env",""))[:80],"planned",datetime.now(timezone.utc).isoformat()))
    return jsonify(id=cur.lastrowid,ok=True,note="Connector registriert; Authentifizierung und API-Endpunkte müssen separat geprüft und freigegeben werden.")

@app.post("/api/site-blueprint")
@admin
def site_blueprint():
    d=request.get_json(silent=True) or {}; url=str(d.get("url","")).strip()
    if d.get("permission") is not True: return jsonify(error="Bitte bestätige Eigentum oder ausdrückliche Berechtigung zur Analyse und Nachbildung."),403
    if not safe_public_url(url): return jsonify(error="Nur öffentlich erreichbare HTTP(S)-Adressen erlaubt; private Netze und lokale Ziele sind gesperrt."),400
    try:
        r=requests.get(url,headers={"User-Agent":"AurealisOmniaSiteAudit/1.0 (authorized site audit)"},timeout=12,allow_redirects=False,stream=True)
        if r.status_code in (301,302,303,307,308): return jsonify(error="Weiterleitungen werden aus Sicherheitsgründen nicht automatisch verfolgt."),400
        if r.status_code>=400:return jsonify(error="Website antwortet mit HTTP "+str(r.status_code)),502
        ctype=r.headers.get("Content-Type","")
        if "text/html" not in ctype:return jsonify(error="Die URL liefert kein HTML-Dokument."),415
        raw=r.raw.read(1_000_000,decode_content=True).decode(r.encoding or "utf-8",errors="replace")
        title=re.search(r"<title[^>]*>(.*?)</title>",raw,re.I|re.S)
        links=re.findall(r"""<a[^>]+href=["']([^"']+)["']""",raw,re.I)
        headings=re.findall(r"<h([1-3])[^>]*>(.*?)</h\1>",raw,re.I|re.S)
        clean=lambda x: re.sub(r"<[^>]+>"," ",x).replace("&amp;","&").strip()[:180]
        blueprint={"url":url,"title":clean(title.group(1)) if title else urlparse(url).netloc,"http_status":r.status_code,"content_type":ctype,"headings":[clean(x[1]) for x in headings[:30]],"navigation_links":links[:60],"captured_at":datetime.now(timezone.utc).isoformat(),"scope":"Nur Startseite/öffentliches HTML; keine Assets, Unterseiten, Login-Bereiche oder fremden Inhalte kopiert."}
        with db() as c:
            cur=c.execute("INSERT INTO projects(name,url,owner_confirmed,blueprint,created_at) VALUES(?,?,?,?,?)",(blueprint["title"],url,1,json.dumps(blueprint,ensure_ascii=False),blueprint["captured_at"]))
        return jsonify(id=cur.lastrowid,blueprint=blueprint)
    except requests.RequestException:return jsonify(error="Website konnte nicht sicher abgerufen werden."),502

@app.get("/api/projects")
@admin
def projects():
    with db() as c: rows=c.execute("SELECT id,name,url,created_at,blueprint FROM projects ORDER BY id DESC").fetchall()
    return jsonify(items=[dict(x) for x in rows])

@app.route("/api/ip-dossiers",methods=["GET","POST"])
@admin
def ip_dossiers():
    if request.method=="GET":
        with db() as c: rows=c.execute("SELECT * FROM ip_dossiers ORDER BY id DESC").fetchall()
        return jsonify(items=[dict(x) for x in rows])
    d=request.get_json(silent=True) or {}; title=str(d.get("title","")).strip()[:180]
    if not title:return jsonify(error="Titel erforderlich."),400
    vals=[title,str(d.get("description",""))[:5000],str(d.get("technical_problem",""))[:2500],str(d.get("solution",""))[:5000],str(d.get("prior_art",""))[:2500],datetime.now(timezone.utc).isoformat()]
    with db() as c: cur=c.execute("INSERT INTO ip_dossiers(title,description,technical_problem,solution,prior_art,created_at) VALUES(?,?,?,?,?,?)",vals)
    return jsonify(id=cur.lastrowid,ok=True,status="draft",note="Nur internes Erfindungsdossier, keine Patentanmeldung und keine Aussage zur Patentfähigkeit.")



_inquiry_hits={}
@app.route("/api/inquiries",methods=["GET","POST"])
def inquiries():
    if request.method=="GET":
        if not session.get("superadmin"): return jsonify(error="Superadmin-Anmeldung erforderlich."),401
        with db() as c: rows=c.execute("SELECT * FROM inquiries ORDER BY id DESC LIMIT 200").fetchall()
        return jsonify(items=[dict(x) for x in rows])
    ip=request.remote_addr or "unknown"; now=monotonic()
    hits=[t for t in _inquiry_hits.get(ip,[]) if now-t<60]
    if len(hits)>=5: return jsonify(error="Zu viele Anfragen. Bitte später erneut versuchen."),429
    hits.append(now); _inquiry_hits[ip]=hits
    d=request.get_json(silent=True) or {}
    name=str(d.get("name","")).strip()[:100]; email=str(d.get("email","")).strip()[:180]
    intent=str(d.get("intent","")).strip()[:40]; asset_type=str(d.get("asset_type","")).strip()[:80]
    title=str(d.get("title","")).strip()[:180]; amount=str(d.get("amount","")).strip()[:80]; message=str(d.get("message","")).strip()[:3000]
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email) or not title or not message:
        return jsonify(error="Bitte Name, gültige E-Mail-Adresse, Titel und Nachricht ausfüllen."),400
    if intent not in {"verkaufen","kaufen","lizenzieren","partnerschaft"}: return jsonify(error="Ungültige Anfrageart."),400
    with db() as c:
        cur=c.execute("INSERT INTO inquiries(name,email,intent,asset_type,title,amount,message,created_at) VALUES(?,?,?,?,?,?,?,?)",(name,email,intent,asset_type,title,amount,message,datetime.now(timezone.utc).isoformat()))
    return jsonify(ok=True,id=cur.lastrowid,note="Anfrage eingegangen. Es wurde kein Kauf, Verkauf oder Anteilstransfer ausgelöst."),201

@app.route("/api/listings",methods=["GET","POST"])
@admin
def listings():
    if request.method=="GET":
        with db() as c: rows=c.execute("SELECT * FROM listings ORDER BY id DESC").fetchall()
        return jsonify(items=[dict(x) for x in rows])
    d=request.get_json(silent=True) or {}; title=str(d.get("title","")).strip()[:180]
    if not title:return jsonify(error="Titel erforderlich."),400
    try: value=max(0,float(d.get("valuation",0))); units=max(0,float(d.get("units",0)))
    except (ValueError,TypeError):return jsonify(error="Wert und Anteile müssen Zahlen sein."),400
    kind=str(d.get("kind","Sachwert"))[:60]; desc=str(d.get("description",""))[:4000]
    with db() as c: cur=c.execute("INSERT INTO listings(title,kind,description,valuation,units,investor_share,platform_share,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(title,kind,desc,value,units,70,30,"demo-only",datetime.now(timezone.utc).isoformat()))
    return jsonify(id=cur.lastrowid,ok=True,status="demo-only",profit_split={"investors_percent":70,"platform_percent":30},note="Keine echten Anteile, Wertpapiere, Token, Zahlungen oder Eigentumsübertragungen.")

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
