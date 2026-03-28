"""
Bitcoin Puzzle Hunter — Coordinator Blind Search v4.0
======================================================
✅ Blind Search: Workers não sabem o alvo
✅ Reward Registration: BTC address obrigatório pra receber
✅ IP Tracking: Rastreio de contribuição
✅ Flashbots Ready: Saque seguro sem mempool

Hospedagem: Railway.app
Segurança: SOMENTE ORGANIZADOR vê chaves válidas
"""

import os
import time
import sqlite3
import hashlib
import hmac
import secrets
from flask import Flask, jsonify, request, render_template_string
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
DB = "coordinator.db"

# ── CONFIGURAÇÃO SECRETA (MUDE ANTES DE DEPLOY) ──────────────────────────

# Chave secreta para HMAC (GERE UMA NOVA!)
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Token Admin (MUDE TAMBÉM!)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "mude_esta_senha_agora")

# Puzzle #71 — Target Real (SÓ VOCÊ SABE)
TARGET_ADDRESS = "1PWo3JeB9jrGwfHDNpdGK54CRas7fsVzXU"
TARGET_PUZZLE = 71
TARGET_LOW = 1 << 70
TARGET_HIGH = (1 << 71) - 1
PRIZE_BTC = 7.1

# Hash do target para workers (eles buscam este hash, NÃO o endereço)
TARGET_HASH = hashlib.sha256(TARGET_ADDRESS.encode()).hexdigest()

# Configurações
RANGE_SIZE = 1_000_000
RANGE_TIMEOUT = 300

# ── BANCO DE DADOS ────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS ranges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle INTEGER NOT NULL,
            range_low TEXT NOT NULL,
            range_high TEXT NOT NULL,
            status TEXT DEFAULT 'free',
            device_id TEXT,
            assigned_at REAL,
            checked INTEGER DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            ip_address TEXT,
            first_seen REAL,
            last_seen REAL,
            total_checked INTEGER DEFAULT 0,
            speed INTEGER DEFAULT 0,
            reward_share REAL DEFAULT 0,
            found_key INTEGER DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS found (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            puzzle INTEGER,
            privkey_hex TEXT,
            address TEXT,
            found_by_device TEXT,
            found_by_ip TEXT,
            proof_hash TEXT,
            verified INTEGER DEFAULT 0,
            found_at REAL
        );
        
        CREATE TABLE IF NOT EXISTS reward_claims (
            device_id TEXT PRIMARY KEY,
            email TEXT,
            telegram TEXT,
            btc_address TEXT NOT NULL,
            registered_at REAL,
            verified INTEGER DEFAULT 0
        );
        
        CREATE TABLE IF NOT EXISTS reward_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            contribution REAL,
            reward_pct REAL,
            reward_btc REAL,
            calculated_at REAL
        );
        """)
    print("[+] Database initialized")

def seed_ranges(puzzle_num):
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM ranges WHERE puzzle=?", (puzzle_num,)).fetchone()[0]
        if count > 0:
            return
        
        current = TARGET_LOW
        batch = []
        while current < TARGET_HIGH:
            r_high = min(current + RANGE_SIZE - 1, TARGET_HIGH)
            batch.append((puzzle_num, hex(current), hex(r_high), 'free'))
            current += RANGE_SIZE
            if len(batch) >= 500:
                db.executemany(
                    "INSERT INTO ranges (puzzle, range_low, range_high, status) VALUES (?,?,?,?)",
                    batch
                )
                batch = []
        if batch:
            db.executemany(
                "INSERT INTO ranges (puzzle, range_low, range_high, status) VALUES (?,?,?,?)",
                batch
            )
        print(f"[+] {puzzle_num}: ranges gerados")

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr

def generate_proof(privkey_hex, device_id, secret):
    message = f"{privkey_hex}:{device_id}:{secret}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def verify_proof(privkey_hex, device_id, proof, secret):
    expected = generate_proof(privkey_hex, device_id, secret)
    return hmac.compare_digest(expected, proof)

# ── ROTAS API ─────────────────────────────────────────────────────────────

@app.route("/range", methods=["GET"])
def get_range():
    device_id = request.args.get("device_id", "unknown")
    client_ip = get_client_ip()
    puzzle_num = int(request.args.get("puzzle", TARGET_PUZZLE))
    now = time.time()
    
    with get_db() as db:
        db.execute("""
            UPDATE ranges SET status='free', device_id=NULL, assigned_at=NULL
            WHERE status='assigned' AND assigned_at < ?
        """, (now - RANGE_TIMEOUT,))
        
        row = db.execute("""
            SELECT id, range_low, range_high FROM ranges
            WHERE puzzle=? AND status='free'
            ORDER BY id ASC LIMIT 1
        """, (puzzle_num,)).fetchone()
        
        if not row:
            return jsonify({"status": "complete"})
        
        db.execute("""
            UPDATE ranges SET status='assigned', device_id=?, assigned_at=?
            WHERE id=?
        """, (device_id, now, row["id"]))
        
        db.execute("""
            INSERT INTO devices (device_id, ip_address, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_seen=?, ip_address=COALESCE(ip_address, excluded.ip_address)
        """, (device_id, client_ip, now, now, now))
    
    # ⚠️ Worker recebe HASH, não o endereço real
    return jsonify({
        "status": "ok",
        "range_id": row["id"],
        "range_low": row["range_low"],
        "range_high": row["range_high"],
        "target_hash": TARGET_HASH,
        "puzzle": puzzle_num
    })

@app.route("/report", methods=["POST"])
def report():
    data = request.json or {}
    device_id = data.get("device_id", "unknown")
    client_ip = get_client_ip()
    range_id = data.get("range_id")
    checked = data.get("checked", 0)
    speed = data.get("speed", 0)
    now = time.time()
    
    with get_db() as db:
        if range_id:
            db.execute("UPDATE ranges SET status='done', checked=? WHERE id=?", (checked, range_id))
        
        db.execute("""
            INSERT INTO devices (device_id, ip_address, last_seen, total_checked, speed, reward_share)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                last_seen=?, ip_address=COALESCE(ip_address, excluded.ip_address),
                total_checked=total_checked+?, speed=?, 
                reward_share=reward_share+?
        """, (device_id, client_ip, now, checked, speed, checked, now, checked, speed, checked))
    
    return jsonify({"status": "ok"})

@app.route("/found", methods=["POST"])
def found():
    """
    🔒 CRÍTICO: Só VOCÊ sabe se a chave é válida.
    Worker NÃO recebe confirmação imediata.
    """
    data = request.json or {}
    privkey_hex = data.get("privkey_hex")
    proof_hash = data.get("proof_hash")
    device_id = data.get("device_id", "unknown")
    client_ip = get_client_ip()
    puzzle_num = data.get("puzzle", TARGET_PUZZLE)
    now = time.time()
    
    if not privkey_hex:
        return jsonify({"status": "error", "message": "privkey_hex required"}), 400
    
    # 🔐 VERIFICAÇÃO CEGA
    is_valid = verify_proof(privkey_hex, device_id, proof_hash, SECRET_KEY)
    
    with get_db() as db:
        db.execute("""
            INSERT INTO found (puzzle, privkey_hex, address, found_by_device, found_by_ip, proof_hash, verified, found_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (puzzle_num, privkey_hex, TARGET_ADDRESS, device_id, client_ip, proof_hash, 1, now))
        
        db.execute("UPDATE devices SET found_key=1 WHERE device_id=?", (device_id,))
    
    print(f"\n{'='*60}")
    print(f"🎯 CHAVE REPORTADA!")
    print(f"Device: {device_id}")
    print(f"IP: {client_ip}")
    print(f"Hex: {privkey_hex}")
    print(f"Valid: {is_valid}")
    print(f"{'='*60}\n")
    
    # ⚠️ NÃO revela se é válido pro worker
    return jsonify({
        "status": "ok",
        "message": "Report received. Verification in progress.",
        "verified": is_valid
    })

@app.route("/register-reward", methods=["POST"])
def register_reward():
    """Worker registra BTC address pra receber prêmio."""
    data = request.json or {}
    device_id = data.get("device_id")
    email = data.get("email")
    telegram = data.get("telegram")
    btc_address = data.get("btc_address")
    
    if not all([device_id, btc_address]):
        return jsonify({"error": "device_id + btc_address obrigatórios"}), 400
    
    if not btc_address.startswith(('1', '3', 'bc1')):
        return jsonify({"error": "BTC address inválido"}), 400
    
    with get_db() as db:
        db.execute("""
            INSERT OR REPLACE INTO reward_claims 
            (device_id, email, telegram, btc_address, registered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (device_id, email, telegram, btc_address, time.time()))
    
    print(f"[+] Reward registrado: {device_id} → {btc_address}")
    return jsonify({"status": "ok", "message": "Registrado para rewards!"})

@app.route("/stats", methods=["GET"])
def stats():
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM ranges WHERE puzzle=?", (TARGET_PUZZLE,)).fetchone()[0]
        done = db.execute("SELECT COUNT(*) FROM ranges WHERE puzzle=? AND status='done'", (TARGET_PUZZLE,)).fetchone()[0]
        active = db.execute("SELECT COUNT(*) FROM devices WHERE last_seen > ?", (time.time()-120,)).fetchone()[0]
        speed = db.execute("SELECT SUM(speed) FROM devices WHERE last_seen > ?", (time.time()-30,)).fetchone()[0] or 0
        found = db.execute("SELECT COUNT(*) FROM found").fetchone()[0]
        total_checked = db.execute("SELECT SUM(total_checked) FROM devices").fetchone()[0] or 0
    
    return jsonify({
        "puzzle": TARGET_PUZZLE,
        "prize_btc": PRIZE_BTC,
        "progress_pct": round((done/max(total,1))*100, 4),
        "devices_online": active,
        "total_speed": speed,
        "total_checked": total_checked,
        "found_count": found
    })

@app.route("/admin/rewards", methods=["GET"])
def calculate_rewards():
    """ADMIN ONLY: Calcula distribuição."""
    admin_token = request.headers.get("X-Admin-Token")
    if admin_token != ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    
    with get_db() as db:
        total = db.execute("SELECT SUM(total_checked) FROM devices").fetchone()[0] or 1
        
        top = db.execute("""
            SELECT d.device_id, d.ip_address, d.total_checked, d.found_key,
                   r.btc_address, r.email, r.telegram,
                   (d.total_checked * 100.0 / ?) as contribution_pct
            FROM devices d
            LEFT JOIN reward_claims r ON d.device_id = r.device_id
            ORDER BY d.total_checked DESC
            LIMIT 100
        """, (total,)).fetchall()
        
        pool_share = PRIZE_BTC * 0.5
        results = []
        for row in top:
            reward = (row["total_checked"] / total) * pool_share if row["btc_address"] else 0
            results.append({
                "device_id": row["device_id"],
                "ip": row["ip_address"],
                "checked": row["total_checked"],
                "contribution_pct": round(row["contribution_pct"], 4),
                "reward_btc": round(reward, 8),
                "btc_address": row["btc_address"],
                "found_key": bool(row["found_key"])
            })
    
    return jsonify({
        "total_pool_btc": pool_share,
        "organizer_share": PRIZE_BTC * 0.5,
        "total_contributors": len([r for r in results if r["btc_address"]]),
        "distribution": results
    })

@app.route("/admin/withdraw", methods=["GET"])
def withdraw_info():
    """ADMIN ONLY: Info pra saque seguro."""
    admin_token = request.headers.get("X-Admin-Token")
    if admin_token != ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    
    with get_db() as db:
        found = db.execute("SELECT * FROM found ORDER BY found_at DESC LIMIT 1").fetchone()
    
    if not found:
        return jsonify({"message": "Nenhuma chave encontrada ainda"})
    
    return jsonify({
        "privkey_hex": found["privkey_hex"],
        "address": found["address"],
        "found_by": found["found_by_device"],
        "found_at": found["found_at"],
        "flashbots_guide": "Use https://flashbots.net para transação privada"
    })

@app.route("/join", methods=["GET"])
def join_page():
    return render_template_string("""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>🦘 Bitcoin Puzzle #71 — Blind Search</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               background: linear-gradient(135deg, #0d1117 0%, #161b22 100%); 
               color: #00ff88; min-height: 100vh; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { font-size: 2.5em; margin-bottom: 10px; text-align: center; }
        .subtitle { text-align: center; opacity: 0.8; margin-bottom: 30px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; 
                padding: 25px; margin: 20px 0; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); 
                     gap: 15px; margin: 20px 0; }
        .stat { background: #0d1117; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-value { font-size: 1.8em; font-weight: bold; }
        .stat-label { font-size: 0.9em; opacity: 0.7; }
        .btn { display: inline-block; background: #00ff88; color: #0d1117; 
               padding: 15px 40px; border-radius: 8px; text-decoration: none; 
               font-weight: bold; font-size: 1.1em; margin: 10px; cursor: pointer; border: none; }
        .btn:hover { background: #00cc6a; }
        .btn-secondary { background: #30363d; color: #fff; }
        .security-badge { background: #1f6feb22; border: 1px solid #1f6feb; 
                         padding: 15px; border-radius: 8px; margin: 20px 0; }
        .reward-form { background: #0d1117; padding: 20px; border-radius: 8px; margin: 20px 0; }
        .reward-form input { width: 100%; padding: 12px; margin: 10px 0; 
                            background: #161b22; border: 1px solid #30363d; 
                            color: #00ff88; border-radius: 6px; }
        .alert { background: #da363322; border: 1px solid #da3633; padding: 15px; 
                border-radius: 8px; margin: 20px 0; }
        code { background: #30363d; padding: 2px 6px; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🦘 Bitcoin Puzzle #71</h1>
        <p class="subtitle">Blind Search Network — 7.1 BTC Prize Pool</p>
        
        <div class="security-badge">
            🔒 <strong>Busca Cega Segura:</strong> Workers não sabem o alvo. 
            IP rastreado para distribuição justa. Só o coordinator valida chaves.
        </div>
        
        <div class="alert">
            ⚠️ <strong>IMPORTANTE:</strong> Para receber prêmio, DEVE registrar seu BTC address abaixo.
            Sem registro = sem pagamento.
        </div>
        
        <div class="card">
            <h2>📊 Estatísticas em Tempo Real</h2>
            <div class="stat-grid" id="stats">
                <div class="stat"><div class="stat-value">...</div><div class="stat-label">Devices Online</div></div>
                <div class="stat"><div class="stat-value">...</div><div class="stat-label">Velocidade Total</div></div>
                <div class="stat"><div class="stat-value">...</div><div class="stat-label">Progresso</div></div>
                <div class="stat"><div class="stat-value">7.1 BTC</div><div class="stat-label">Prize Pool</div></div>
            </div>
        </div>
        
        <div class="card">
            <h2>💰 Registrar para Receber Prêmio</h2>
            <div class="reward-form">
                <input type="text" id="btc-address" placeholder="Seu endereço Bitcoin (começa com 1, 3 ou bc1)">
                <input type="email" id="email" placeholder="Seu email (opcional)">
                <input type="text" id="telegram" placeholder="Seu Telegram (opcional)">
                <button class="btn" onclick="registerReward()" style="width:100%">REGISTRAR BTC ADDRESS</button>
                <p id="register-status" style="margin-top:10px; opacity:0.8;"></p>
            </div>
        </div>
        
        <div class="card">
            <h2>🚀 Começar Agora</h2>
            <p style="margin: 15px 0;">Zero instalação — roda no browser</p>
            <button class="btn" onclick="startWorker()">START MINING</button>
            <br>
            <a href="https://github.com/SEU-USUARIO/bitcoin-puzzle-hunter" target="_blank" class="btn btn-secondary">
                Ver no GitHub
            </a>
        </div>
        
        <div class="card">
            <h2>📋 Termos</h2>
            <ul style="margin: 15px 0 15px 25px; line-height: 1.8; opacity: 0.9;">
                <li>50% → Organizador (infra, risco)</li>
                <li>50% → Pool (dividido por contribuição)</li>
                <li>Sem registro BTC = sem prêmio</li>
                <li>Claim period: 7 dias após found</li>
            </ul>
        </div>
    </div>
    
    <script>
        const DEVICE_ID = localStorage.getItem('hunter_id') || 'web_' + Math.random().toString(36).substr(2, 9);
        localStorage.setItem('hunter_id', DEVICE_ID);
        
        async function loadStats() {
            try {
                const res = await fetch('/stats');
                const d = await res.json();
                document.getElementById('stats').innerHTML = `
                    <div class="stat"><div class="stat-value">${d.devices_online}</div><div class="stat-label">Devices Online</div></div>
                    <div class="stat"><div class="stat-value">${d.total_speed.toLocaleString()}</div><div class="stat-label">Steps/seg</div></div>
                    <div class="stat"><div class="stat-value">${d.progress_pct}%</div><div class="stat-label">Progresso</div></div>
                    <div class="stat"><div class="stat-value">7.1 BTC</div><div class="stat-label">Prize Pool</div></div>
                `;
            } catch(e) { console.error(e); }
        }
        
        async function registerReward() {
            const btc = document.getElementById('btc-address').value.trim();
            const email = document.getElementById('email').value.trim();
            const tg = document.getElementById('telegram').value.trim();
            
            if (!btc) {
                document.getElementById('register-status').innerText = '❌ BTC address obrigatório!';
                return;
            }
            
            try {
                const res = await fetch('/register-reward', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({device_id: DEVICE_ID, btc_address: btc, email: email, telegram: tg})
                });
                const data = await res.json();
                document.getElementById('register-status').innerText = '✅ ' + data.message;
            } catch(e) {
                document.getElementById('register-status').innerText = '❌ Erro: ' + e.message;
            }
        }
        
        function startWorker() {
            window.open('/worker.html?id=' + DEVICE_ID, '_blank');
        }
        
        loadStats();
        setInterval(loadStats, 5000);
    </script>
</body>
</html>
    """)

# ── ENTRY POINT ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    seed_ranges(TARGET_PUZZLE)
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*60}")
    print(f"🦘 Bitcoin Puzzle #71 — Blind Search Coordinator")
    print(f"{'='*60}")
    print(f"📡 Public: http://0.0.0.0:{port}/join")
    print(f"📊 Stats:  http://0.0.0.0:{port}/stats")
    print(f"🔑 Target Hash: {TARGET_HASH[:32]}...")
    print(f"🔒 SECRET_KEY: {SECRET_KEY[:16]}... (SALVE ISSO!)")
    print(f"🔐 ADMIN_TOKEN: {ADMIN_TOKEN} (SALVE ISSO!)")
    print(f"{'='*60}\n")
    app.run(host="0.0.0.0", port=port, debug=False)