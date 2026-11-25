import os, re, json, sqlite3, secrets, ipaddress, socket
from time import monotonic
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse
import requests
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

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
        CREATE TABLE IF NOT EXISTS membership_programs(
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS member_profiles(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','mentor','admin')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS membership_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            program_slug TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            price TEXT NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES member_profiles(id)
        );
        CREATE TABLE IF NOT EXISTS member_entitlements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            program_slug TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            starts_at TEXT NOT NULL,
            ends_at TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(user_id,program_slug),
            FOREIGN KEY(user_id) REFERENCES member_profiles(id)
        );
        CREATE TABLE IF NOT EXISTS characters(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,role TEXT,traits TEXT,voice TEXT,background TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS connectors(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,base_url TEXT NOT NULL,auth_env TEXT,status TEXT DEFAULT 'planned',created_at TEXT);
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,url TEXT NOT NULL,owner_confirmed INTEGER,blueprint TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS platform_registry(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,url TEXT NOT NULL,category TEXT,description TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS ip_dossiers(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,description TEXT,technical_problem TEXT,solution TEXT,prior_art TEXT,status TEXT DEFAULT 'draft',created_at TEXT);
        CREATE TABLE IF NOT EXISTS inquiries(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT NOT NULL,intent TEXT,asset_type TEXT,title TEXT,amount TEXT,message TEXT,status TEXT DEFAULT 'new',created_at TEXT);
        CREATE TABLE IF NOT EXISTS listings(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,kind TEXT,description TEXT,valuation REAL,units REAL,investor_share REAL DEFAULT 70,platform_share REAL DEFAULT 30,status TEXT DEFAULT 'demo-only',created_at TEXT);
        CREATE TABLE IF NOT EXISTS tracked_wallets(address TEXT PRIMARY KEY,label TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS portfolio_assets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_type TEXT NOT NULL CHECK(asset_type IN ('crypto','stock')),
            asset_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            average_cost REAL NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'EUR',
            created_at TEXT NOT NULL,
            UNIQUE(asset_type,asset_key)
        );
        """)
        default_address=os.getenv("BITCOIN_WALLET_ADDRESS","bc1q4q7lujkr8mcv9e7suet524uxjfwlaxm7aelq5y").strip()
        if re.fullmatch(r"bc1[ac-hj-np-z02-9]{39,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}",default_address):
            c.execute("INSERT OR IGNORE INTO tracked_wallets(address,label,created_at) VALUES(?,?,?)",(default_address,"Hauptwallet",datetime.now(timezone.utc).isoformat()))
init_db()
with db() as c:
    for program in [
        ("aurealis-omnia","AUREALIS OMNIA","Bitcoin-, Coin-, Aktien- und KI-Finanzhub"),
        ("ultima-one","ULTIMA ONE","Zentrale Unternehmens- und Plattformverwaltung"),
        ("quantum-mirror","Quantum Mirror","KI- und Erlebnisplattform"),
        ("neuroforge","Neuroforge","Forschungs- und Entwicklungsplattform"),
        ("ate-rea","ATE REA","Spirituelle Chroniken, Bücher und Community"),
        ("krypto-aurora","Krypto-Aurora","Krypto-Analyse und Marktübersicht"),
        ("aurealis-trading","Aurealis Trading","Trading- und Portfolio-Werkzeuge"),
        ("aurealis-shopping-ai","Aurealis Shopping AI","KI-gestützte Produktsuche"),
        ("characterverse","Characterverse","Charakter- und Profilwelt"),
        ("quantum-shield","Quantum Shield","Sicherheits- und Schutzwerkzeuge")
    ]:
        c.execute("INSERT OR IGNORE INTO membership_programs(slug,name,description,created_at) VALUES(?,?,?,?)",(*program,datetime.now(timezone.utc).isoformat()))


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

@app.get("/wallet")
def wallet_page(): return send_from_directory("frontend","wallet.html")

def valid_btc_address(address):
    return bool(re.fullmatch(r"bc1[ac-hj-np-z02-9]{39,59}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}",address))

def fetch_wallet_data(address):
    r=requests.get("https://mempool.space/api/address/"+address,timeout=15)
    r.raise_for_status()
    data=r.json()
    chain=data.get("chain_stats",{}); pending=data.get("mempool_stats",{})
    received=int(chain.get("funded_txo_sum",0)); spent=int(chain.get("spent_txo_sum",0))
    pending_received=int(pending.get("funded_txo_sum",0)); pending_spent=int(pending.get("spent_txo_sum",0))
    txr=requests.get("https://mempool.space/api/address/"+address+"/txs",timeout=15)
    txr.raise_for_status()
    txs=txr.json()[:15]
    history=[{"txid":t.get("txid"),"confirmed":bool(t.get("status",{}).get("confirmed")),"block_time":t.get("status",{}).get("block_time"),"block_height":t.get("status",{}).get("block_height")} for t in txs]
    return {"address":address,"confirmed_received_sats":received,"confirmed_spent_sats":spent,"confirmed_balance_sats":received-spent,"pending_received_sats":pending_received,"pending_spent_sats":pending_spent,"pending_balance_sats":pending_received-pending_spent,"tx_count":int(chain.get("tx_count",0)),"history":history,"source":"mempool.space","read_only":True,"ownership_verified":False}

@app.get("/api/wallet")
def wallet_public_data():
    address=request.args.get("address",os.getenv("BITCOIN_WALLET_ADDRESS","bc1q4q7lujkr8mcv9e7suet524uxjfwlaxm7aelq5y")).strip()
    if not valid_btc_address(address):
        return jsonify(error="Bitcoin-Adresse ist formal nicht gültig."),400
    try:
        return jsonify(fetch_wallet_data(address))
    except requests.RequestException:
        return jsonify(error="Blockchain-Daten sind momentan nicht erreichbar. Bitte später erneut laden."),502
    except (ValueError,TypeError,KeyError):
        return jsonify(error="Die Blockchain-Antwort konnte nicht verarbeitet werden."),502

@app.route("/api/wallets",methods=["GET","POST"])
def tracked_wallets():
    if request.method=="POST":
        data=request.get_json(silent=True) or {}
        address=str(data.get("address","")).strip()
        label=str(data.get("label","")).strip()[:80] or "Wallet"
        if not valid_btc_address(address):
            return jsonify(error="Bitte eine gültige Bitcoin-Adresse (bc1…, 1… oder 3…) eingeben."),400
        try:
            with db() as c:
                c.execute("INSERT INTO tracked_wallets(address,label,created_at) VALUES(?,?,?)",(address,label,datetime.now(timezone.utc).isoformat()))
            return jsonify(ok=True,address=address,label=label),201
        except sqlite3.IntegrityError:
            return jsonify(error="Diese Bitcoin-Adresse ist bereits in der Übersicht eingetragen."),409
    with db() as c:
        rows=c.execute("SELECT address,label,created_at FROM tracked_wallets ORDER BY created_at ASC").fetchall()
    items=[]
    for row in rows:
        item=dict(row)
        try:
            item.update(fetch_wallet_data(item["address"]))
            item["available"]=True
        except requests.RequestException:
            item.update(available=False,error="Blockchain-Daten momentan nicht erreichbar.")
        except (ValueError,TypeError,KeyError):
            item.update(available=False,error="Blockchain-Antwort konnte nicht verarbeitet werden.")
        items.append(item)
    totals={key:sum(int(x.get(key,0)) for x in items if x.get("available")) for key in ("confirmed_balance_sats","confirmed_received_sats","confirmed_spent_sats","pending_balance_sats")}
    return jsonify(items=items,totals=totals,source="mempool.space",read_only=True,ownership_verified=False)


_mining_ai_hits={}
def mempool_get(path):
    r=requests.get("https://mempool.space/api/"+path.lstrip("/"),headers={"User-Agent":"AurealisOmniaBlockMonitor/1.0"},timeout=18)
    r.raise_for_status()
    return r.json()

@app.get("/api/mining/overview")
def mining_overview():
    try:
        tip_response=requests.get("https://mempool.space/api/blocks/tip/height",headers={"User-Agent":"AurealisOmniaBlockMonitor/1.0"},timeout=18)
        tip_response.raise_for_status()
        height=int(tip_response.text.strip())
        blocks=mempool_get("blocks/"+str(max(0,height-11)))
        mempool=mempool_get("mempool")
        hashrate=mempool_get("v1/mining/hashrate/1m")
        pools=mempool_get("v1/mining/pools/1m")
        safe_blocks=[]
        for b in blocks[:12]:
            safe_blocks.append({"id":b.get("id"),"height":b.get("height"),"timestamp":b.get("timestamp"),"tx_count":b.get("tx_count"),"size":b.get("size"),"weight":b.get("weight"),"median_fee":b.get("median_fee"),"reward":b.get("extras",{}).get("reward"),"pool":(b.get("extras",{}).get("pool") or {}).get("name","Unbekannter Mining-Pool")})
        pool_items=[]
        for p in (pools.get("pools") or [])[:8]:
            pool_items.append({"name":p.get("name"),"block_count":p.get("blockCount"),"match_rate":p.get("matchRate")})
        current_hashrate=hashrate.get("currentHashrate")
        current_difficulty=hashrate.get("currentDifficulty")
        return jsonify({"tip_height":height,"blocks":safe_blocks,"mempool":{"count":mempool.get("count"),"vsize":mempool.get("vsize"),"total_fee":mempool.get("total_fee")},"mining":{"current_hashrate":current_hashrate,"current_difficulty":current_difficulty,"pools":pool_items},"analysis":{"status":"live","summary":"Die Kennzahlen stammen aus öffentlichen Bitcoin-Netzwerkdaten. Hashrate und Difficulty beschreiben das Gesamtnetzwerk, nicht die Rechenleistung deines Computers oder einen persönlichen Mining-Ertrag."},"source":"mempool.space","read_only":True,"updated_at":datetime.now(timezone.utc).isoformat()})
    except (requests.RequestException,ValueError,TypeError,KeyError) as e:
        app.logger.warning("Mining overview unavailable: %s",type(e).__name__)
        return jsonify(error="Live-Blockchain-Daten sind momentan nicht erreichbar. Bitte erneut aktualisieren.",source="mempool.space"),502

@app.get("/api/mining/hardware/status")
def mining_hardware_status():
    """Read-only bridge to a user-operated ASIC gateway; no miner control or wallet signing."""
    gateway=os.getenv("MINING_GATEWAY_URL","").strip().rstrip("/")
    token=os.getenv("MINING_GATEWAY_TOKEN","").strip()
    if not gateway:
        return jsonify(configured=False,connected=False,status="not_configured",
                       required_environment=["MINING_GATEWAY_URL","MINING_GATEWAY_TOKEN"],
                       note="A public blockchain explorer is not an ASIC miner. Connect a user-operated, authenticated HTTPS gateway to report hardware telemetry."),200
    parsed=urlparse(gateway)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
        return jsonify(configured=False,connected=False,status="invalid_configuration",
                       error="MINING_GATEWAY_URL must be an HTTPS URL without embedded credentials."),503
    if not token:
        return jsonify(configured=False,connected=False,status="missing_gateway_token",
                       required_environment=["MINING_GATEWAY_TOKEN"]),503
    try:
        response=requests.get(gateway+"/api/status",
            headers={"Authorization":"Bearer "+token,"Accept":"application/json"},
            timeout=8)
        response.raise_for_status()
        payload=response.json()
        if not isinstance(payload,dict):
            raise ValueError("invalid_gateway_payload")
        allowed=("connected","miner","model","hashrate","hashrate_unit","power_w",
                 "temperature_c","pool","shares_accepted","shares_rejected","uptime_seconds")
        metrics={key:payload[key] for key in allowed if key in payload}
        return jsonify(configured=True,connected=bool(payload.get("connected",True)),
                       status="connected" if payload.get("connected",True) else "disconnected",
                       hardware=metrics,source="configured_user_gateway",read_only=True,
                       control_enabled=False,wallet_signing=False,
                       updated_at=datetime.now(timezone.utc).isoformat()),200
    except requests.RequestException:
        app.logger.warning("ASIC gateway unavailable")
        return jsonify(configured=True,connected=False,status="gateway_unreachable",
                       error="Der konfigurierte ASIC-Gateway ist nicht erreichbar. URL, TLS, Firewall und Gateway-Logs prüfen."),502
    except (ValueError,TypeError):
        return jsonify(configured=True,connected=False,status="invalid_gateway_response",
                       error="Der ASIC-Gateway lieferte keine gültige JSON-Statusantwort."),502


@app.get("/api/mining/block-height/<int:height>")
def mining_block_height(height):
    if height<0 or height>3000000:
        return jsonify(error="Ungültige Blockhöhe."),400
    try:
        block_hash=mempool_get("block-height/"+str(height))
        if not re.fullmatch(r"[a-fA-F0-9]{64}",str(block_hash)):
            return jsonify(error="Für diese Blockhöhe wurde kein Block gefunden."),404
        return jsonify(height=height,block_hash=block_hash)
    except requests.RequestException:
        return jsonify(error="Blockhöhe konnte momentan nicht abgefragt werden."),502

@app.get("/api/mining/block/<block_hash>")
def mining_block_detail(block_hash):
    if not re.fullmatch(r"[a-fA-F0-9]{64}",block_hash):
        return jsonify(error="Ungültiger Block-Hash."),400
    try:
        block=mempool_get("block/"+block_hash)
        txs=mempool_get("block/"+block_hash+"/txs")
        details=[]
        for tx in txs[:30]:
            vin=sum(int(v.get("prevout",{}).get("value",0) or 0) for v in tx.get("vin",[]))
            vout=sum(int(v.get("value",0) or 0) for v in tx.get("vout",[]))
            details.append({"txid":tx.get("txid"),"fee":tx.get("fee"),"size":tx.get("size"),"weight":tx.get("weight"),"confirmed":bool(tx.get("status",{}).get("confirmed")),"input_sats":vin,"output_sats":vout})
        return jsonify({"block":{"id":block.get("id"),"height":block.get("height"),"timestamp":block.get("timestamp"),"tx_count":block.get("tx_count"),"size":block.get("size"),"weight":block.get("weight"),"version":block.get("version"),"nonce":block.get("nonce"),"bits":block.get("bits"),"difficulty":block.get("difficulty"),"merkle_root":block.get("merkle_root"),"previousblockhash":block.get("previousblockhash"),"median_fee":block.get("extras",{}).get("medianFee"),"pool":(block.get("extras",{}).get("pool") or {}).get("name","Unbekannter Mining-Pool"),"reward":block.get("extras",{}).get("reward")},"transactions":details,"transactions_shown":len(details),"note":"Es werden maximal 30 Transaktionen pro Block in der Detailansicht angezeigt; der Explorer-Link öffnet den vollständigen Block."})
    except requests.RequestException:
        return jsonify(error="Blockdetails konnten momentan nicht geladen werden."),502


@app.get("/api/mining/transaction/<txid>")
def mining_transaction_detail(txid):
    if not re.fullmatch(r"[a-fA-F0-9]{64}",txid):
        return jsonify(error="Ungültige Transaktions-ID."),400
    try:
        tx=mempool_get("tx/"+txid)
        status=tx.get("status") or {}
        inputs=[]
        for v in (tx.get("vin") or [])[:100]:
            prev=v.get("prevout") or {}
            inputs.append({"txid":v.get("txid"),"vout":v.get("vout"),"address":prev.get("scriptpubkey_address"),"value_sats":int(prev.get("value",0) or 0),"is_coinbase":bool(v.get("is_coinbase"))})
        outputs=[]
        for v in (tx.get("vout") or [])[:100]:
            outputs.append({"index":v.get("n"),"address":v.get("scriptpubkey_address"),"value_sats":int(v.get("value",0) or 0),"script_type":v.get("scriptpubkey_type")})
        return jsonify({"txid":tx.get("txid"),"version":tx.get("version"),"locktime":tx.get("locktime"),"size":tx.get("size"),"weight":tx.get("weight"),"fee_sats":tx.get("fee"),"status":{"confirmed":bool(status.get("confirmed")),"block_height":status.get("block_height"),"block_time":status.get("block_time")},"inputs":inputs,"outputs":outputs,"inputs_shown":len(inputs),"outputs_shown":len(outputs),"source":"mempool.space","note":"Eingänge und Ausgänge sind öffentliche Transaktionsdaten. Ein angezeigter Blockreward ist nicht automatisch eine Auszahlung an den Betrachter."})
    except requests.RequestException:
        return jsonify(error="Transaktionsdetails sind momentan nicht erreichbar."),502

@app.post("/api/mining/ai-analysis")
def mining_ai_analysis():
    now=monotonic(); ip=request.remote_addr or "unknown"
    hits=[t for t in _mining_ai_hits.get(ip,[]) if now-t<60]
    if len(hits)>=3:
        return jsonify(error="Bitte warte eine Minute vor der nächsten KI-Analyse."),429
    hits.append(now); _mining_ai_hits[ip]=hits
    if not API_KEY:
        return jsonify(error="Die Mining-KI benötigt OPENAI_API_KEY in den Render-Environment-Variablen. Der Live-Block-Explorer funktioniert unabhängig davon."),503
    data=request.get_json(silent=True) or {}
    metrics=data.get("metrics") if isinstance(data.get("metrics"),dict) else {}
    allowed={"tip_height","blocks","mempool","mining"}
    metrics={k:metrics[k] for k in allowed if k in metrics}
    prompt="Analysiere diese aktuellen öffentlichen Bitcoin-Netzwerkdaten auf Deutsch. Erkläre Blockaktivität, Transaktionsstau, Gebühren, Difficulty/Hashrate und Mining-Pools. Trenne Messwerte von Interpretation. Keine Rendite- oder Gewinnversprechen. Sage ausdrücklich, dass es Netzwerkwerte und kein eigenes Mining sind. Daten: "+json.dumps(metrics,ensure_ascii=False)[:14000]
    try:
        r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+API_KEY,"Content-Type":"application/json"},json={"model":MODEL,"instructions":"Du bist AUREALIS OMNIA Mining Intelligence. Du interpretierst ausschließlich übermittelte öffentliche Bitcoin-Blockchain-Metriken. Erfinde keine Werte. Gib eine kurze, sachliche technische Analyse mit Abschnitt Netzwerkstatus, Blockanalyse, Mining-Leistung des Gesamtnetzwerks und Bedeutung für den Nutzer. Behaupte niemals, dass das Dashboard Bitcoin mined oder dass der Nutzer Mining-Rewards verdient.","input":prompt,"store":False},timeout=45)
        if not r.ok:
            app.logger.error("Mining AI API error %s",r.status_code)
            return jsonify(error="Die KI-Analyse ist derzeit nicht verfügbar. Bitte Modellzugriff und API-Kontingent prüfen."),502
        obj=r.json(); answer=[]
        for item in obj.get("output",[]):
            if item.get("type")=="message":
                for part in item.get("content",[]):
                    if part.get("type")=="output_text": answer.append(part.get("text",""))
        return jsonify(answer="\n".join(answer).strip(),model=obj.get("model",MODEL),generated_at=datetime.now(timezone.utc).isoformat())
    except requests.RequestException:
        return jsonify(error="Verbindung zur KI-Analyse fehlgeschlagen."),502


_market_hits={}
_prediction_hits={}
def market_json(url,params=None):
    r=requests.get(url,params=params,headers={"User-Agent":"AurealisOmniaMarkets/1.0"},timeout=18)
    r.raise_for_status()
    return r.json()

def yahoo_search(query):
    data=market_json("https://query1.finance.yahoo.com/v1/finance/search",{"q":query,"quotesCount":12,"newsCount":0})
    out=[]
    for q in data.get("quotes",[]):
        if q.get("quoteType") not in ("EQUITY","ETF","MUTUALFUND","INDEX"):
            continue
        out.append({"asset_type":"stock","asset_key":q.get("symbol"),"symbol":q.get("symbol"),"name":q.get("shortname") or q.get("longname") or q.get("symbol"),"exchange":q.get("exchDisp") or q.get("exchange"),"currency":q.get("currency") or "USD"})
    return out

def yahoo_quote(symbol):
    safe=str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=_-]{1,24}",safe):
        raise ValueError("Ungültiges Aktiensymbol.")
    data=market_json("https://query1.finance.yahoo.com/v8/finance/chart/"+safe,{"range":"5d","interval":"1d"})
    result=(data.get("chart",{}).get("result") or [None])[0]
    if not result: raise ValueError("Keine Kursdaten für dieses Symbol gefunden.")
    meta=result.get("meta",{})
    price=meta.get("regularMarketPrice")
    if price is None: raise ValueError("Aktueller Kurs nicht verfügbar.")
    prev=meta.get("chartPreviousClose") or meta.get("previousClose") or price
    change=(float(price)-float(prev))/float(prev)*100 if prev else 0
    return {"asset_type":"stock","asset_key":safe,"symbol":safe,"name":meta.get("longName") or meta.get("shortName") or safe,"price":float(price),"currency":meta.get("currency") or "USD","change_24h_pct":change,"exchange":meta.get("exchangeName"),"source":"Yahoo Finance","updated_at":datetime.now(timezone.utc).isoformat()}

@app.get("/api/markets/search")
def market_search():
    q=str(request.args.get("q","")).strip()[:80]
    kind=request.args.get("type","crypto").lower()
    if len(q)<2:return jsonify(items=[])
    if kind not in ("crypto","stock"):return jsonify(error="Typ muss crypto oder stock sein."),400
    try:
        if kind=="crypto":
            data=market_json("https://api.coingecko.com/api/v3/search",{"query":q})
            items=[{"asset_type":"crypto","asset_key":x.get("id"),"symbol":str(x.get("symbol","")).upper(),"name":x.get("name"),"market_cap_rank":x.get("market_cap_rank"),"thumb":x.get("thumb")} for x in data.get("coins",[])[:15]]
        else:
            items=yahoo_search(q)
        return jsonify(items=items,source="CoinGecko" if kind=="crypto" else "Yahoo Finance")
    except requests.RequestException:
        return jsonify(error="Marktsuche momentan nicht erreichbar. Bitte später erneut versuchen."),502

@app.get("/api/markets/universe")
def market_universe():
    kind=request.args.get("type","crypto").lower()
    if kind!="crypto":return jsonify(error="Für Aktien nutze die dynamische Symbolsuche; der Aktienmarkt hat keine einheitliche vollständige kostenlose Gesamtliste."),400
    try:
        page=max(1,min(4,int(request.args.get("page","1"))))
        data=market_json("https://api.coingecko.com/api/v3/coins/markets",{"vs_currency":"eur","order":"market_cap_desc","per_page":250,"page":page,"sparkline":"false","price_change_percentage":"24h,7d"})
        items=[{"asset_type":"crypto","asset_key":x.get("id"),"symbol":str(x.get("symbol","")).upper(),"name":x.get("name"),"price":x.get("current_price"),"currency":"EUR","market_cap":x.get("market_cap"),"rank":x.get("market_cap_rank"),"change_24h_pct":x.get("price_change_percentage_24h"),"change_7d_pct":x.get("price_change_percentage_7d_in_currency"),"image":x.get("image")} for x in data]
        return jsonify(items=items,page=page,per_page=250,source="CoinGecko",note="Top 1.000 nach Marktkapitalisierung; weitere Coins können über die Suche gefunden werden.")
    except (requests.RequestException,ValueError):
        return jsonify(error="Coin-Marktdaten momentan nicht verfügbar."),502

@app.get("/api/markets/quote")
def market_quote():
    kind=request.args.get("type","crypto").lower()
    key=str(request.args.get("id" if kind=="crypto" else "symbol","")).strip()
    if kind not in ("crypto","stock") or not key:return jsonify(error="Asset-Typ und Kennung erforderlich."),400
    try:
        if kind=="crypto":
            if not re.fullmatch(r"[a-z0-9-]{1,100}",key):return jsonify(error="Ungültige Coin-Kennung."),400
            data=market_json("https://api.coingecko.com/api/v3/coins/markets",{"vs_currency":"eur","ids":key,"sparkline":"false","price_change_percentage":"24h,7d"})
            if not data:return jsonify(error="Coin nicht gefunden."),404
            x=data[0]
            return jsonify({"asset_type":"crypto","asset_key":x.get("id"),"symbol":str(x.get("symbol","")).upper(),"name":x.get("name"),"price":x.get("current_price"),"currency":"EUR","market_cap":x.get("market_cap"),"volume_24h":x.get("total_volume"),"change_24h_pct":x.get("price_change_percentage_24h"),"change_7d_pct":x.get("price_change_percentage_7d_in_currency"),"source":"CoinGecko","updated_at":datetime.now(timezone.utc).isoformat()})
        return jsonify(yahoo_quote(key))
    except ValueError as e:return jsonify(error=str(e)),400
    except requests.RequestException:return jsonify(error="Kursdaten momentan nicht erreichbar."),502

@app.route("/api/portfolio/assets",methods=["GET","POST"])
def portfolio_assets():
    if request.method=="POST":
        d=request.get_json(silent=True) or {}
        kind=str(d.get("asset_type","")).lower(); key=str(d.get("asset_key","")).strip().lower() if kind=="crypto" else str(d.get("asset_key","")).strip().upper()
        symbol=str(d.get("symbol","")).strip().upper()[:24]; name=str(d.get("name","")).strip()[:120]
        try: qty=float(d.get("quantity",0)); cost=float(d.get("average_cost",0))
        except (TypeError,ValueError):return jsonify(error="Menge und durchschnittlicher Einstandskurs müssen Zahlen sein."),400
        currency=str(d.get("currency","EUR")).upper()[:8]
        if kind not in ("crypto","stock") or not key or len(key)>100 or not symbol or not name or not (0<=qty<1e18) or not (0<=cost<1e18):
            return jsonify(error="Bitte Asset, gültige Menge und Einstandskurs prüfen."),400
        try:
            with db() as c:
                cur=c.execute("INSERT INTO portfolio_assets(asset_type,asset_key,symbol,name,quantity,average_cost,currency,created_at) VALUES(?,?,?,?,?,?,?,?)",(kind,key,symbol,name,qty,cost,currency,datetime.now(timezone.utc).isoformat()))
            return jsonify(ok=True,id=cur.lastrowid),201
        except sqlite3.IntegrityError:return jsonify(error="Dieses Asset ist bereits im gemeinsamen Portfolio. Bitte den bestehenden Eintrag verwalten."),409
    with db() as c: rows=c.execute("SELECT * FROM portfolio_assets ORDER BY asset_type,name").fetchall()
    return jsonify(items=[dict(x) for x in rows],shared=True,note="Gemeinsame Portfolioübersicht: Einträge sind für alle Besucher sichtbar.")

@app.delete("/api/portfolio/assets/<int:asset_id>")
def delete_portfolio_asset(asset_id):
    with db() as c: cur=c.execute("DELETE FROM portfolio_assets WHERE id=?",(asset_id,))
    if cur.rowcount==0:return jsonify(error="Eintrag nicht gefunden."),404
    return jsonify(ok=True,deleted_id=asset_id)

@app.post("/api/markets/prediction")
def market_prediction():
    now=monotonic(); ip=request.remote_addr or "unknown"
    hits=[t for t in _prediction_hits.get(ip,[]) if now-t<60]
    if len(hits)>=3:return jsonify(error="Bitte warte eine Minute bis zur nächsten Prognose."),429
    hits.append(now);_prediction_hits[ip]=hits
    if not API_KEY:return jsonify(error="Für die Vorhersage-KI muss OPENAI_API_KEY in Render Environment gesetzt sein."),503
    d=request.get_json(silent=True) or {}; assets=d.get("assets") if isinstance(d.get("assets"),list) else []
    assets=assets[:25]
    clean=[]
    for a in assets:
        if not isinstance(a,dict):continue
        clean.append({k:a.get(k) for k in ("asset_type","asset_key","symbol","name","price","currency","change_24h_pct","change_7d_pct","market_cap","volume_24h","quantity","average_cost") if k in a})
    if not clean:return jsonify(error="Keine aktuellen Kursdaten zur Analyse übermittelt."),400
    prompt="Erstelle auf Deutsch eine vorsichtige, datenbasierte Szenarioanalyse für diese Assets. Bewerte je Asset 1 Woche, 1 Monat und 1 Jahr als bullisches, neutrales und bärisches Szenario, nenne Einflussfaktoren, Unsicherheiten und welche zusätzlichen Daten fehlen. Nutze ausschließlich die übermittelten Messwerte plus allgemeines Wissen; erfinde keine Live-Nachrichten, Kurse oder Wahrscheinlichkeiten. Keine garantierten Kursziele, keine personalisierte Kauf-/Verkaufsempfehlung. Daten (JSON): "+json.dumps(clean,ensure_ascii=False)[:18000]
    try:
        r=requests.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+API_KEY,"Content-Type":"application/json"},json={"model":MODEL,"instructions":"Du bist AUREALIS OMNIA Market Intelligence. Deine Prognosen sind Szenarien, keine Gewissheiten. Trenne aktuelle Marktdaten, Annahmen und Spekulation. Keine Anlageberatung, keine Renditeversprechen, keine erfundenen Daten. Weise auf Volatilität und Totalverlustrisiko hin, insbesondere bei kleinen Coins.","input":prompt,"store":False},timeout=60)
        if not r.ok:
            app.logger.error("Market prediction API error %s",r.status_code)
            return jsonify(error="Vorhersage-KI nicht verfügbar. Bitte API-Schlüssel, Modellzugriff und Kontingent prüfen."),502
        obj=r.json(); out=[]
        for item in obj.get("output",[]):
            if item.get("type")=="message":
                for part in item.get("content",[]):
                    if part.get("type")=="output_text":out.append(part.get("text",""))
        return jsonify(answer="\\n".join(out).strip(),model=obj.get("model",MODEL),generated_at=datetime.now(timezone.utc).isoformat(),kind="scenario_analysis")
    except requests.RequestException:return jsonify(error="Verbindung zur Vorhersage-KI fehlgeschlagen."),502

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


# Membership: each registered program has its own entitlement; mentor access is granted only by the superadmin.
PROGRAM_CATALOG = [
    {"slug":"aurealis-omnia","name":"AUREALIS OMNIA","description":"Bitcoin-, Coin-, Aktien- und KI-Finanzhub"},
    {"slug":"ultima-one","name":"ULTIMA ONE","description":"Zentrale Unternehmens- und Plattformverwaltung"},
    {"slug":"quantum-mirror","name":"Quantum Mirror","description":"KI- und Erlebnisplattform"},
    {"slug":"neuroforge","name":"Neuroforge","description":"Forschungs- und Entwicklungsplattform"},
    {"slug":"ate-rea","name":"ATE REA","description":"Spirituelle Chroniken, Bücher und Community"},
    {"slug":"krypto-aurora","name":"Krypto-Aurora","description":"Krypto-Analyse und Marktübersicht"},
    {"slug":"aurealis-trading","name":"Aurealis Trading","description":"Trading- und Portfolio-Werkzeuge"},
    {"slug":"aurealis-shopping-ai","name":"Aurealis Shopping AI","description":"KI-gestützte Produktsuche"},
    {"slug":"characterverse","name":"Characterverse","description":"Charakter- und Profilwelt"},
    {"slug":"quantum-shield","name":"Quantum Shield","description":"Sicherheits- und Schutzwerkzeuge"}
]
def all_programs():
    with db() as c: rows=c.execute("SELECT slug,name,description FROM membership_programs ORDER BY name").fetchall()
    return [dict(x) for x in rows]

MEMBERSHIP_PLANS = {
    "global_annual": {"name":"Global Premium", "price":"3000.00", "currency":"USD", "period":"year", "monthly_equivalent":"250.00 USD", "scope":"Alle teilnehmenden Programme"},
    "program_monthly": {"name":"Programm Premium", "price":"10.00", "currency":"EUR", "period":"month", "monthly_equivalent":"10.00 EUR", "scope":"Ein ausgewähltes Programm"},
    "global_lifetime": {"name":"All-Access Lifetime", "price":"333.00", "currency":"EUR", "period":"lifetime", "monthly_equivalent":None, "scope":"Alle teilnehmenden Programme"}
}
def current_member():
    uid=session.get("member_id")
    if not uid:return None
    with db() as c: row=c.execute("SELECT id,email,display_name,role,created_at FROM member_profiles WHERE id=?",(uid,)).fetchone()
    return dict(row) if row else None

def member_required(fn):
    @wraps(fn)
    def wrapped(*a,**k):
        member=current_member()
        if not member:return jsonify(error="Bitte zuerst ein Profil registrieren oder anmelden."),401
        return fn(member,*a,**k)
    return wrapped

@app.get("/api/membership/plans")
def membership_plans():
    return jsonify(programs=all_programs(),plans=MEMBERSHIP_PLANS,
        rules={"mentor":"Lebenslanger Zugang zu allen teilnehmenden Programmen; nur durch Superadmin vergebbar.","member":"Premiumzugang erst nach bestätigter Zahlung und Freigabe.","per_program":"Jedes Programm besitzt eine getrennte Berechtigung."},
        payment_status="manual_review_required",
        note="Es ist noch kein Zahlungsanbieter verbunden. Eine Anfrage aktiviert keinen Premiumzugang automatisch.")

@app.post("/api/membership/register")
def membership_register():
    d=request.get_json(silent=True) or {}
    email=str(d.get("email","")).strip().lower()
    name=str(d.get("display_name","")).strip()[:80]
    password=str(d.get("password",""))
    if not re.fullmatch(r"[^@\s]{1,100}@[^@\s]{1,180}\.[^@\s]{2,30}",email):return jsonify(error="Bitte eine gültige E-Mail-Adresse eingeben."),400
    if len(password)<12:return jsonify(error="Das Passwort muss mindestens 12 Zeichen lang sein."),400
    if not name:return jsonify(error="Bitte einen Profilnamen angeben."),400
    try:
        with db() as c:
            cur=c.execute("INSERT INTO member_profiles(email,password_hash,display_name,role,created_at) VALUES(?,?,?,?,?)",
                (email,generate_password_hash(password),name,"member",datetime.now(timezone.utc).isoformat()))
        session["member_id"]=cur.lastrowid
        return jsonify(ok=True,member=current_member(),note="Profil erstellt. Mentor- und Premiumrechte werden niemals durch Registrierung selbst vergeben."),201
    except sqlite3.IntegrityError:return jsonify(error="Für diese E-Mail-Adresse besteht bereits ein Profil."),409

@app.post("/api/membership/login")
def membership_login():
    d=request.get_json(silent=True) or {}; email=str(d.get("email","")).strip().lower(); password=str(d.get("password",""))
    with db() as c: row=c.execute("SELECT id,password_hash FROM member_profiles WHERE email=?",(email,)).fetchone()
    if not row or not check_password_hash(row["password_hash"],password):return jsonify(error="E-Mail oder Passwort ist nicht korrekt."),401
    session["member_id"]=row["id"]
    return jsonify(ok=True,member=current_member())

@app.post("/api/membership/logout")
def membership_logout():
    session.pop("member_id",None)
    return jsonify(ok=True)

@app.get("/api/membership/me")
@member_required
def membership_me(member):
    with db() as c:
        ent=c.execute("SELECT program_slug,plan_code,status,starts_at,ends_at FROM member_entitlements WHERE user_id=? AND status='active'",(member["id"],)).fetchall()
        reqs=c.execute("SELECT id,program_slug,plan_code,price,currency,status,created_at FROM membership_requests WHERE user_id=? ORDER BY id DESC LIMIT 30",(member["id"],)).fetchall()
    mentor=member["role"]=="mentor"
    return jsonify(member=member,mentor_lifetime=mentor,entitlements=[dict(x) for x in ent],requests=[dict(x) for x in reqs],
        effective_access="all_programs_lifetime" if mentor else "individual_entitlements")

@app.post("/api/membership/request")
@member_required
def membership_request(member):
    d=request.get_json(silent=True) or {}
    slug=str(d.get("program_slug","")).strip()
    plan=str(d.get("plan_code","")).strip()
    if plan not in MEMBERSHIP_PLANS:return jsonify(error="Unbekannter Tarif."),400
    if slug!="all-programs" and slug not in {x["slug"] for x in all_programs()}:return jsonify(error="Unbekanntes Programm."),400
    if plan=="program_monthly" and slug=="all-programs":return jsonify(error="Der Monatstarif gilt für genau ein Programm."),400
    if plan in {"global_annual","global_lifetime"} and slug!="all-programs":return jsonify(error="Dieser Tarif gilt für alle teilnehmenden Programme."),400
    if member["role"]=="mentor":return jsonify(ok=True,note="Mentorstatus: lebenslanger Zugang ist bereits aktiv; kein Abo erforderlich."),200
    p=MEMBERSHIP_PLANS[plan]
    with db() as c:
        existing=c.execute("SELECT id FROM membership_requests WHERE user_id=? AND program_slug=? AND plan_code=? AND status='pending'",(member["id"],slug,plan)).fetchone()
        if existing:return jsonify(error="Für diesen Tarif liegt bereits eine offene Anfrage vor."),409
        cur=c.execute("INSERT INTO membership_requests(user_id,program_slug,plan_code,price,currency,status,created_at) VALUES(?,?,?,?,?,'pending',?)",
            (member["id"],slug,plan,p["price"],p["currency"],datetime.now(timezone.utc).isoformat()))
    return jsonify(ok=True,request_id=cur.lastrowid,status="pending",price=p["price"],currency=p["currency"],
        note="Anfrage gespeichert. Noch keine Zahlung, Abbuchung oder Freischaltung. Zahlungsabwicklung und manuelle Prüfung sind noch nicht verbunden."),201

@app.route("/api/membership/admin/programs",methods=["GET","POST"])
@admin
def membership_admin_programs():
    if request.method=="GET":return jsonify(items=all_programs())
    d=request.get_json(silent=True) or {}
    slug=re.sub(r"[^a-z0-9-]+","-",str(d.get("slug","")).strip().lower()).strip("-")[:80]
    name=str(d.get("name","")).strip()[:120]
    description=str(d.get("description","")).strip()[:500]
    if not slug or not name:return jsonify(error="Slug und Programmname sind erforderlich."),400
    with db() as c:
        try:c.execute("INSERT INTO membership_programs(slug,name,description,created_at) VALUES(?,?,?,?)",(slug,name,description,datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:return jsonify(error="Dieses Programm ist bereits registriert."),409
    return jsonify(ok=True,program={"slug":slug,"name":name,"description":description}),201

@app.get("/api/membership/admin/requests")
@admin
def membership_admin_requests():
    with db() as c:
        rows=c.execute("SELECT r.*,m.email,m.display_name FROM membership_requests r JOIN member_profiles m ON m.id=r.user_id ORDER BY r.id DESC LIMIT 200").fetchall()
    return jsonify(items=[dict(x) for x in rows])

@app.post("/api/membership/admin/approve/<int:request_id>")
@admin
def membership_admin_approve(request_id):
    with db() as c:
        r=c.execute("SELECT * FROM membership_requests WHERE id=? AND status='pending'",(request_id,)).fetchone()
        if not r:return jsonify(error="Offene Anfrage nicht gefunden."),404
        now=datetime.now(timezone.utc)
        ends=None
        if r["plan_code"]=="program_monthly":
            from datetime import timedelta
            ends=(now+timedelta(days=30)).isoformat()
        elif r["plan_code"]=="global_annual":
            from datetime import timedelta
            ends=(now+timedelta(days=365)).isoformat()
        c.execute("UPDATE membership_requests SET status='approved' WHERE id=?",(request_id,))
        if r["plan_code"] in {"global_annual","global_lifetime"}:
            for program in all_programs():
                c.execute("INSERT INTO member_entitlements(user_id,program_slug,plan_code,status,starts_at,ends_at,created_at) VALUES(?,?,?,'active',?,?,?) ON CONFLICT(user_id,program_slug) DO UPDATE SET plan_code=excluded.plan_code,status='active',starts_at=excluded.starts_at,ends_at=excluded.ends_at",
                    (r["user_id"],program["slug"],r["plan_code"],now.isoformat(),ends,now.isoformat()))
        else:
            c.execute("INSERT INTO member_entitlements(user_id,program_slug,plan_code,status,starts_at,ends_at,created_at) VALUES(?,?,?,'active',?,?,?) ON CONFLICT(user_id,program_slug) DO UPDATE SET plan_code=excluded.plan_code,status='active',starts_at=excluded.starts_at,ends_at=excluded.ends_at",
                (r["user_id"],r["program_slug"],r["plan_code"],now.isoformat(),ends,now.isoformat()))
    return jsonify(ok=True,status="approved",note="Zugang aktiviert. Vor Freigabe muss die Zahlung außerhalb dieses Systems geprüft worden sein.")

@app.post("/api/membership/admin/mentor/<int:user_id>")
@admin
def membership_admin_mentor(user_id):
    d=request.get_json(silent=True) or {}; action=str(d.get("action","grant"))
    if action not in {"grant","revoke"}:return jsonify(error="Aktion muss grant oder revoke sein."),400
    with db() as c:
        row=c.execute("SELECT id FROM member_profiles WHERE id=?",(user_id,)).fetchone()
        if not row:return jsonify(error="Profil nicht gefunden."),404
        role="mentor" if action=="grant" else "member"
        c.execute("UPDATE member_profiles SET role=? WHERE id=?",(role,user_id))
        if action=="grant":
            now=datetime.now(timezone.utc).isoformat()
            for program in PROGRAM_CATALOG:
                c.execute("INSERT INTO member_entitlements(user_id,program_slug,plan_code,status,starts_at,ends_at,created_at) VALUES(?,?,?,'active',?,NULL,?) ON CONFLICT(user_id,program_slug) DO UPDATE SET plan_code='mentor_lifetime',status='active',ends_at=NULL",
                    (user_id,program["slug"],"mentor_lifetime",now,now))
        else:
            c.execute("UPDATE member_entitlements SET status='inactive' WHERE user_id=? AND plan_code='mentor_lifetime'",(user_id,))
    return jsonify(ok=True,role=role,mentor_lifetime=(action=="grant"))

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


@app.route("/api/platform-registry",methods=["GET","POST"])
@admin
def platform_registry():
    with db() as c:
        if request.method=="GET":
            rows=c.execute("SELECT * FROM platform_registry ORDER BY category,name").fetchall()
            return jsonify(items=[dict(x) for x in rows])
        d=request.get_json(silent=True) or {}
        name=str(d.get("name","")).strip()[:120]; url=str(d.get("url","")).strip()[:500]
        category=str(d.get("category","Weitere Projekte")).strip()[:80]
        description=str(d.get("description","")).strip()[:500]
        if not name or not safe_public_url(url): return jsonify(error="Name und gültige öffentliche HTTP(S)-URL erforderlich."),400
        exists=c.execute("SELECT id FROM platform_registry WHERE url=?",(url,)).fetchone()
        if exists:return jsonify(error="Diese Website ist bereits registriert."),409
        cur=c.execute("INSERT INTO platform_registry(name,url,category,description,created_at) VALUES(?,?,?,?,?)",(name,url,category,description,datetime.now(timezone.utc).isoformat()))
    return jsonify(ok=True,id=cur.lastrowid,status="registered")

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
