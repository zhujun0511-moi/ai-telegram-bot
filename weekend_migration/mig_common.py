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
        self.dead_keys = set()                        # 當日額度耗盡的 key index（跨批次共用，見 DANGER_ZONES §9）

    def _pick_key(self):
        """round-robin 選下一把「今天還沒耗盡」的 key，回傳 index；全部耗盡回 None。"""
        n = len(self.keys)
        for _ in range(n):
            self.ki = (self.ki + 1) % n
            if self.ki not in self.dead_keys:
                return self.ki
        return None

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
        # 主動 round-robin 選一把未耗盡的 key；全部 key 當日額度已耗盡 → 快速失敗、連請求都不發（不燒額度）
        if self._pick_key() is None:
            return {s: {"status": "error", "message": "TD 當日額度已全部耗盡（放棄不重試）"} for s in symbols}
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
            # 額度/限流
            if isinstance(data, dict) and data.get("status") == "error":
                msg = str(data.get("message", ""))
                low = msg.lower()
                if "limit" in low or "credit" in low or data.get("code") == 429:
                    # 「per minute」＝當分鐘限流（暫時，換 key + 稍候重試）；其餘 credit/limit 錯誤
                    # （如「current limit 800」）＝當日額度耗盡，retry 只會多燒（見 DANGER_ZONES §9）
                    if "minute" in low:
                        if len(self.keys) > 1:
                            self._pick_key()
                            params["apikey"] = self.keys[self.ki]
                        last_err = f"每分鐘限流: {scrub(msg)}"
                        time.sleep(5 + attempt * 5)
                        continue
                    # 當日額度耗盡：這把 key 今天報廢，換一把還沒耗盡的續抓；全耗盡 → 立即放棄不 retry
                    self.dead_keys.add(self.ki)
                    if self._pick_key() is None:
                        last_err = f"當日額度耗盡（全部 key，放棄不重試）: {scrub(msg)}"
                        break
                    params["apikey"] = self.keys[self.ki]
                    last_err = f"當日額度耗盡，換 key 續抓: {scrub(msg)}"
                    continue
                last_err = f"TD error: {scrub(msg)}"
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


# ── 身份權威（Ticker_Identity）+ 日曆 + identity_break 清理 ────────────────
def load_identity_map(stock_db):
    """回傳 {TICKER: list_date}（身份權威，供 bar_validator identity_break 用）。"""
    out = {}
    for d in stock_db["Ticker_Identity"].find({}):
        tk = d.get("ticker") or d.get("_id")
        if isinstance(tk, str) and d.get("list_date"):
            out[tk.upper()] = d["list_date"]
    return out


def load_calendar(stock_db):
    """回傳 {holidays:set, half_days:set}（日期權威）。"""
    doc = stock_db["Configs"].find_one({"type": "market_calendar"}) or {}
    return {"holidays": set(doc.get("holidays", [])),
            "half_days": set(h.get("date") for h in doc.get("half_days", []) if isinstance(h, dict))}


def completed_trading_date(calendar, today=None):
    """最近「已完成」交易日：保守從昨天起往前找第一個平日-非假日（不認今天的 forming）。
    週末跑 Sat→Fri。平日盤後想認今天需自行覆蓋。"""
    import datetime
    holidays = (calendar or {}).get("holidays", set())
    today = today or datetime.date.today()
    d = today - datetime.timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d.isoformat()
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def identity_severity(existing_d_bars, list_date, calendar=None):
    """用 bar_validator 判 identity_break：回傳 'severe' | 'gray' | 'mild' | None（純規則，不呼叫 AI）。
    'gray'＝近門檻、規則說不準，呼叫端（identity_cleanup）再用 ai_adjudicate 問免費模型定奪。
    以「現有 D bars」判（污染在既有資料裡，非新抓的 TD）。"""
    if not list_date:
        return None
    import bar_validator as bv
    v = bv.validate(existing_d_bars, {"period": "D", "operation": "historical_backfill",
                                      "list_date": list_date, "calendar": calendar or {}})
    if any(x["code"] == "identity_break_severe" for x in v["hard_fails"]):
        return "severe"
    if any(x["code"] == "needs_adjudication" for x in v["warnings"]):
        return "gray"
    if any(x["code"] == "identity_break_mild" for x in v["warnings"]):
        return "mild"
    return None


def ai_adjudicate(case, timeout=20):
    """灰色地帶 identity 案例交 AC 免費模型判疑：回 'reuse' | 'keep' | None。
    None＝AI 不可用/失敗 → 呼叫端保守處理（當 mild 保留，不誤刪）。
    走 BC→AC /validator/adjudicate（AC 管 free_models_registry；BC→AC HTTP 可行、非 Space→Space）。
    case: {ticker, company_name, jump_pct, vol_ratio, list_date, pre_sample[], post_sample[]}
    """
    base = os.getenv("ANALYSIS_HUB_URL", "").strip()
    secret = os.getenv("WEBHOOK_SECRET", "").strip() or env("WEBHOOK_SECRET", required=False)
    if not base or not secret:
        print("[ai_adjudicate] ANALYSIS_HUB_URL/WEBHOOK_SECRET 未設定 → 回 None（保守）")
        return None
    try:
        r = requests.post(base.rstrip("/") + "/validator/adjudicate", json=case,
                          headers={"WEBHOOK_SECRET": secret, "Content-Type": "application/json"},
                          timeout=timeout)
        if r.status_code == 200:
            verdict = str((r.json() or {}).get("verdict", "")).lower().strip()
            if verdict in ("reuse", "keep"):
                return verdict
            print(f"[ai_adjudicate] AC 回無法解析的 verdict: {verdict!r}")
        else:
            print(f"[ai_adjudicate] AC 回 {r.status_code}")
    except Exception as e:
        print(f"[ai_adjudicate] 異常: {scrub(e)}")
    return None


def write_validation_log(stock_db, context, period, operation, scope, outcomes, write=True):
    """runner 層寫一筆 Validation_Log 稽核（append-only；純核心 validate() 不碰 IO）。
    outcomes: [{ticker, action('replaced'/'skipped'/'blocked'/'archived'/'kept'), latest?, codes?}]。
    - dry-run（write=False）或唯讀 URI 寫不進：印出該寫的 summary，回 False，不擋主流程。
    - 目的：以後要再抓時有參照——查得到上次誰成功/誰被跳過/為什麼。
    """
    import datetime
    from collections import Counter
    act_ct = Counter(o.get("action", "?") for o in outcomes)
    code_ct = Counter(c for o in outcomes for c in o.get("codes", []))
    doc = {
        "run_at": datetime.datetime.now(datetime.timezone.utc),
        "context": context, "period": period, "operation": operation,
        "scope": scope,
        "summary": {"actions": dict(act_ct), "codes": dict(code_ct), "n": len(outcomes)},
        "ok": act_ct.get("blocked", 0) == 0,
        # 只留「非單純成功」的逐票細節（capped），避免每票一行塞爆
        "outcomes": [o for o in outcomes if o.get("action") != "replaced"][:150],
    }
    if not write:
        print(f"[validation_log] （dry-run 未寫）context={context} summary={doc['summary']}")
        return False
    try:
        stock_db["Validation_Log"].insert_one(dict(doc))
        print(f"[validation_log] ✅ 已寫 Validation_Log｜context={context} summary={doc['summary']}")
        return True
    except Exception as e:
        print(f"[validation_log] ⚠️ 寫入失敗（可能唯讀 URI）: {scrub(e)}")
        print(f"[validation_log] 該寫 summary: {doc['summary']}")
        return False


def archive_prelisting(stock_db, tk, period, list_date, write):
    """把現有 Bars_{tk} period 裡 t<list_date 的舊實體 bar 複製封存到 Bars_{tk}_prelisting
    （不刪原 collection，只複製；供回溯）。回傳封存根數。"""
    import datetime
    doc = stock_db[f"Bars_{tk}"].find_one({"period": period}) or {}
    pre = [b for b in doc.get("bars", []) if b.get("t", "")[:10] < list_date]
    if not pre:
        return 0
    if write:
        stock_db[f"Bars_{tk}_prelisting"].update_one(
            {"ticker": tk, "period": period},
            {"$set": {"ticker": tk, "period": period, "bars": pre,
                      "list_date": list_date, "reason": "identity_reuse",
                      "archived_at": datetime.datetime.now(datetime.timezone.utc)}},
            upsert=True)
    return len(pre)
