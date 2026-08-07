# -*- coding: utf-8 -*-
"""
mig_common.py — 週末數據遷移共用基礎（2026-08-05 寫，本地跑、不 push）

計畫：ai/script/腳本匯總/handoff_subsystem_intraday_hbar_sourcing.md §14.4/14.5
用途：D/H backfill via Twelve Data、W resample、備份、驗證 共用的 env/Mongo/TD 工具。

env（從環境變數讀；若未設，讀 MIG_ENV_FILE 指向的檔案，正則抓 KEY=VALUE / mongodb URI，
不預覽、不硬編碼——見 memory feedback_secret_file_handling）：
  MONGO_URI, TWELVE_KEY, TWELVE_KEY_2（第二把，額度接續）
"""
import os, re, time
import requests

# ── 密鑰遮蔽（2026-08-06，見 DANGER_ZONES 2026-08-05 §5）─────────────
# TDClient 用 requests(..., params={apikey:...})，連線例外的 str(e) 會帶出含 apikey 的整條 URL。
_QS_SECRET_RE = re.compile(
    r'(?i)(apikey|api_key|secret|token|password|pwd|access_token|sig|signature)=([^&\s"\'#]+)')
_MONGO_CRED_RE = re.compile(r'(mongodb(?:\+srv)?://)[^:/@\s]+:[^@\s]+@')
_SECRET_ENV_NAMES = ("MONGO_URI", "TWELVE_KEY", "TWELVE_KEY_2", "POLYGON_KEY", "HF_TOKEN", "GH_TOKEN")

def scrub(s):
    """遮蔽字串中的密鑰值（query-param 密鑰 / mongodb URI 帳密 / 已知 env 密鑰值）。"""
    if s is None:
        return s
    try:
        s = str(s)
    except Exception:
        return "***"
    # env 精確值先替換（含特殊字元/密碼含 @ 也整段遮；必須在正則前，否則正則改寫後精確比對失配）
    for _name in _SECRET_ENV_NAMES:
        _val = os.getenv(_name, "") or _env_cache.get(_name, "")
        if _val and len(_val) >= 8:
            s = s.replace(_val, "***")
    s = _QS_SECRET_RE.sub(r'\1=***', s)
    s = _MONGO_CRED_RE.sub(r'\1***:***@', s)
    return s

# ── env 載入（不預覽、格式無關）─────────────────────────────
_env_cache = {}

def _load_env_file():
    path = os.getenv("MIG_ENV_FILE", "").strip()
    if not path or not os.path.isfile(path):
        return
    raw = open(path, "r", encoding="utf-8-sig", errors="replace").read()
    # mongodb 連線字串
    m = re.search(r"mongodb(?:\+srv)?://[^\s'\"]+", raw)
    if m:
        _env_cache["MONGO_URI"] = m.group(0).strip().strip("\"'")
    # KEY = VALUE / KEY : VALUE（半形或全形冒號都收）
    for k, v in re.findall(r"^([A-Z][A-Z0-9_]{2,})\s*[=:：]\s*(\S+)", raw, re.MULTILINE):
        _env_cache.setdefault(k, v.strip().strip("\"'"))

def env(name, required=True):
    v = os.getenv(name, "").strip()
    if v:
        return v
    if not _env_cache:
        _load_env_file()
    v = _env_cache.get(name, "")
    if not v and required:
        raise RuntimeError(f"缺 env: {name}（設環境變數，或 MIG_ENV_FILE 指向含它的檔）")
    return v

# ── Mongo ────────────────────────────────────────────────
def get_stock_db():
    import pymongo
    cli = pymongo.MongoClient(env("MONGO_URI"), serverSelectionTimeoutMS=8000)
    cli.admin.command("ping")
    return cli, cli["StockData"]

def get_all_tickers(stock_db):
    cfg = stock_db["Configs"].find_one({"type": "ticker_lists"})
    return (cfg or {}).get("full_set", []) if cfg else []

# ── Twelve Data ──────────────────────────────────────────
TD_URL = "https://api.twelvedata.com/time_series"
RTH_SLOTS = {f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 30)
             if not (h == 9 and m == 0)}  # 09:30..15:30
# 註：09:00 排除；15:30 是最後一根（15:30-16:00 段）。與現有 Bars_H 一致。

def _td_keys():
    keys = [env("TWELVE_KEY")]
    k2 = env("TWELVE_KEY_2", required=False)
    if k2:
        keys.append(k2)
    return keys

class TDClient:
    """TD 抓取 + 雙 key 輪替 + 節流。free tier: 8 req/min、~800 credits/day/key。"""
    def __init__(self, req_interval_s=8.0, credits_per_min_per_key=7.0):
        self.keys = _td_keys()
        self.ki = 0
        self.req_interval = req_interval_s            # 全域間隔下限（保底）
        self.cpm_per_key = credits_per_min_per_key    # 每 key 每分鐘 credit 上限（免費層硬限 8，留 margin 用 7）
        self._last = 0.0

    def _throttle(self, n_symbols=1):
        # ⚠️ TD 批次每個 symbol 算 1 credit → 一次請求耗 n_symbols credits（原節流誤設每請求 1 credit，
        #    BATCH=8 時 8 秒吃 8 credits → 撞免費層「8 credits/分鐘」，見 DANGER_ZONES）。
        # 守「每 key ≤ cpm_per_key credits/分鐘」：單 key 最小間隔 = n_symbols/cpm_per_key 分鐘；
        # k 把 key 主動 round-robin 均攤 → 全域間隔可再 ÷k。取與保底下限較大者。
        k = max(1, len(self.keys))
        need = (n_symbols * 60.0 / self.cpm_per_key) / k
        interval = max(self.req_interval, need)
        dt = time.time() - self._last
        if dt < interval:
            time.sleep(interval - dt)
        self._last = time.time()

    def fetch(self, symbols, interval, outputsize=None, start_date=None, end_date=None,
              retries=3):
        """
        symbols: list（≤8 建議）；interval: '1day'/'30min'/'1week' 等。
        回傳 { SYMBOL: {'status':'ok','values':[{datetime,open,high,low,close,volume},...]} }。
        （單票時 TD 回傳扁平，這裡正規化成同樣的 dict）
        """
        if not symbols:
            return {}
        # 主動 round-robin：每次 fetch 換下一把 key，讓多把 key 均攤（反應式「撞限流才換」會讓單 key 先過載）
        self.ki = (self.ki + 1) % len(self.keys)
        sym_str = ",".join(symbols)
        params = {"symbol": sym_str, "interval": interval, "apikey": self.keys[self.ki]}
        if outputsize is not None:
            params["outputsize"] = str(outputsize)
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        last_err = None
        for attempt in range(retries):
            self._throttle(len(symbols))
            try:
                r = requests.get(TD_URL, params=params, timeout=30)
                data = r.json()
            except Exception as e:
                # ⚠️ scrub：params 含 apikey，連線例外 str(e) 會帶出整條含 key 的 URL
                last_err = f"HTTP/JSON 異常: {scrub(e)}"
                time.sleep(2 + attempt * 3)
                continue
            # 額度/限流 → 換 key 重試
            if isinstance(data, dict) and data.get("status") == "error":
                msg = str(data.get("message", ""))
                if "limit" in msg.lower() or "credit" in msg.lower() or data.get("code") == 429:
                    self.ki = (self.ki + 1) % len(self.keys)
                    params["apikey"] = self.keys[self.ki]
                    last_err = f"限流/額度: {msg}"
                    time.sleep(5 + attempt * 5)
                    continue
                last_err = f"TD error: {msg}"
                break
            # 正規化：單票扁平 → {sym: {...}}
            if len(symbols) == 1 and "values" in data:
                return {symbols[0]: data}
            return data
        return {s: {"status": "error", "message": last_err or "unknown"} for s in symbols}


def td_values_to_bars(values, period):
    """
    TD values（新在前）→ Bars schema [{t,o,h,l,c,v}]（新在前）。
    period: 'D' → t='YYYY-MM-DD 00:00:00'；'H' → RTH 過濾、t='YYYY-MM-DD HH:MM:SS'。
    """
    out = []
    for v in values or []:
        dt = v.get("datetime", "")
        if not dt:
            continue
        if period == "D":
            day = dt[:10]
            t = f"{day} 00:00:00"
        elif period == "H":
            if len(dt) < 16:
                continue
            hhmm = dt[11:16]
            if hhmm not in RTH_SLOTS:      # 只留 RTH（治 LC 曾存非交易時段的教訓）
                continue
            t = dt if len(dt) >= 19 else f"{dt[:16]}:00"
        else:
            t = dt
        try:
            bar = {
                "t": t,
                "o": float(v["open"]),
                "h": float(v["high"]),
                "l": float(v["low"]),
                "c": float(v["close"]),
                "v": float(v.get("volume") or 0),
            }
        except (KeyError, ValueError, TypeError):
            continue
        out.append(bar)
    # 去重（同 t 留第一筆＝TD 新在前的最新）+ 保持新在前
    seen = set(); dedup = []
    for b in out:
        if b["t"] in seen:
            continue
        seen.add(b["t"]); dedup.append(b)
    dedup.sort(key=lambda b: b["t"], reverse=True)
    return dedup


def batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
