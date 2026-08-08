# -*- coding: utf-8 -*-
"""
bar_validator.py — 統一數據驗證器純核心（2026-08-08）

設計文件：ai/script/腳本匯總/handoff_task_data_validator.md
定位：**純函數、零 IO、零副作用**。只判「資料對不對」，回 verdict；
      retry / skip / 縮水守門 / 清理 / 告警 / 寫 log ＝呼叫端（送檢方）的政策，不在這裡。

⚠️ canonical 副本：本檔為權威版。之後 vendored 進 DC 一份，**逐字同步**（見設計文件「落地位置」）。
   audit 腳本可 checksum 比對兩份是否走樣。

用法：
    v = validate(bars, spec)
    if not v["ok"]:
        ... 處理 v["hard_fails"] ...
    ... v["warnings"] 記錄即可 ...

spec 欄位（皆選填，validator 只驗「有給的」）：
    period        : "D"|"W"|"H"|"M"|"15m"   （驅動 period 對齊檢查）
    operation     : "daily_incremental"|"historical_backfill"|"synthesis"（帶目的驗）
    expect_latest : "YYYY-MM-DD"  最近已完成交易日（daily_incremental 要求 latest==此）
    calendar      : {"holidays": set/list["YYYY-MM-DD"], "half_days": set/list["YYYY-MM-DD"]}
    date_range    : ("YYYY-MM-DD","YYYY-MM-DD")  連續性檢查範圍（缺則用資料自身首尾）
    list_date     : "YYYY-MM-DD"  Ticker_Identity 身份權威（identity_break 用）
    ticker        : str  （僅用於 verdict 標註）
"""

# ── 可調門檻（2026-08-08 用 SPCX/LUNR/QBTS/LAC 全宇宙真實資料標定）─────────────
IDENTITY_SEVERE_JUMP_PCT  = 0.80   # 身份接縫價格跳幅 >80% → severe（不同公司/SPAC 借殼跳空）
IDENTITY_SEVERE_VOL_RATIO = 20.0   # 身份接縫量級 regime 比 >20x 或 <1/20 → severe
IDENTITY_GRAY_JUMP_PCT    = 0.40   # jump 落在 (0.40, 0.80] ＝灰色（低於明確 severe 但不算連續）→ 交 AI 判疑
IDENTITY_GRAY_VOL_RATIO   = 8.0    # vol_ratio 落在 (8, 20] 或 [1/20, 1/8) ＝灰色 → 交 AI 判疑
BIG_JUMP_WARN_PCT         = 0.50   # 一般相鄰大跳幅 → warning（可能分割，只標記不硬擋）
SEAM_WINDOW               = 10     # 接縫兩側各取幾根算量級中位


# 寫入應硬擋的 hard_fail 代碼。calendar_gap 對 historical_backfill 深歷史屬諮詢性
# （TD 連續資料可信、深歷史超日曆涵蓋），不硬擋覆蓋；bad_ohlc 另按「數量」判（見下）。
WRITE_BLOCKING_CODES = {
    "empty", "bad_structure", "bad_type", "nonpositive_price",
    "negative_volume", "future_bar", "duplicate_t", "not_desc_sorted",
    "identity_break_severe",
}
# 孤立幾根壞 OHLC（如單一餵數故障日 2023-06-05）不擋 backfill 覆蓋——TD 是最佳可得、
# 舊資料同樣壞或更差。只有「系統性」(≥此門檻) 才判整份不可信、擋住覆蓋。
BAD_OHLC_BLOCK_COUNT = 5


def blocking_fails(verdict):
    """回傳 verdict 裡「應擋住寫入」的 hard_fails（供呼叫端當寫入閘門）。
    bad_ohlc 孤立不擋、系統性(≥BAD_OHLC_BLOCK_COUNT)才擋。"""
    hf = verdict.get("hard_fails", [])
    blocking = [x for x in hf if x["code"] in WRITE_BLOCKING_CODES]
    ohlc_ct = sum(1 for x in hf if x["code"] == "bad_ohlc")
    if ohlc_ct >= BAD_OHLC_BLOCK_COUNT:
        blocking.append({"code": "bad_ohlc", "count": ohlc_ct})
    return blocking


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    n = len(xs)
    if n == 0:
        return 0.0
    m = n // 2
    return xs[m] if n % 2 else (xs[m - 1] + xs[m]) / 2.0


def _as_set(v):
    if v is None:
        return set()
    return v if isinstance(v, set) else set(v)


def _prev_trading_day_str(date_str, holidays):
    """回傳 date_str 前一個交易日（跳過週末 + holidays）。純字串日期運算。"""
    import datetime
    d = datetime.date.fromisoformat(date_str)
    for _ in range(10):
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d.isoformat()
    return d.isoformat()


def _seam_metrics(bars, list_date):
    """
    bars 新在前。回傳 (price_jump_pct, vol_ratio)：
      price_jump_pct = |新實體最舊 close − 舊實體最新 close| / 舊實體最新 close
      vol_ratio      = 新實體接縫側量中位 / 舊實體接縫側量中位
    無法計算的回 (0.0, 1.0)。
    """
    pre  = [b for b in bars if b.get("t", "")[:10] < list_date]   # 舊實體（較舊）
    post = [b for b in bars if b.get("t", "")[:10] >= list_date]  # 新實體（較新）
    if not pre or not post:
        return 0.0, 1.0
    pre_newest_c  = pre[0].get("c")     # 新在前 → pre[0] 是舊實體最新（最接近接縫）
    post_oldest_c = post[-1].get("c")   # post[-1] 是新實體最舊（最接近接縫）
    jump = 0.0
    if pre_newest_c and post_oldest_c and pre_newest_c != 0:
        jump = abs(post_oldest_c - pre_newest_c) / abs(pre_newest_c)
    mv_post = _median([b.get("v", 0) for b in post[-SEAM_WINDOW:]])
    mv_pre  = _median([b.get("v", 0) for b in pre[:SEAM_WINDOW]])
    if mv_pre and mv_pre > 0:
        vol_ratio = mv_post / mv_pre
    else:
        vol_ratio = float("inf") if mv_post else 1.0
    return jump, vol_ratio


def validate(bars, spec=None):
    """驗證一支 ticker 的一個 period 的 bars。回傳 verdict dict。"""
    spec = spec or {}
    hard_fails, warnings = [], []
    ticker = spec.get("ticker", "?")
    period = spec.get("period")
    operation = spec.get("operation", "daily_incremental")
    cal = spec.get("calendar") or {}
    holidays = _as_set(cal.get("holidays"))

    def hf(code, **kw): hard_fails.append(dict(code=code, **kw))
    def wn(code, **kw): warnings.append(dict(code=code, **kw))

    # ── 空資料 ──
    if not bars:
        hf("empty", msg="bars 為空")
        return {"ok": False, "ticker": ticker, "hard_fails": hard_fails, "warnings": warnings}

    # ── 通用：結構 + OHLC 合理性 ──
    prev_t = None
    seen_t = set()
    dup = 0
    unsorted = False
    for b in bars:
        t = b.get("t")
        if not t or any(k not in b for k in ("o", "h", "l", "c", "v")):
            hf("bad_structure", msg="缺 t/o/h/l/c/v")
            return {"ok": False, "ticker": ticker, "hard_fails": hard_fails, "warnings": warnings}
        try:
            o, h, l, c, v = float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b["v"])
        except (TypeError, ValueError):
            hf("bad_type", t=t, msg="OHLCV 非數值")
            continue
        if not (h >= l and h >= max(o, c) and l <= min(o, c)):
            hf("bad_ohlc", t=t, o=o, h=h, l=l, c=c)
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            hf("nonpositive_price", t=t)
        if v < 0:
            hf("negative_volume", t=t)
        if t in seen_t:
            dup += 1
        seen_t.add(t)
        if prev_t is not None and t > prev_t:
            unsorted = True     # 新在前 → 後面的 t 不該比前面大
        prev_t = t
    if dup:
        hf("duplicate_t", count=dup)
    if unsorted:
        hf("not_desc_sorted", msg="bars 應新在前（t 遞減）")

    latest = bars[0].get("t", "")[:10]
    oldest = bars[-1].get("t", "")[:10]

    # ── 無未來 bar ──
    expect_latest = spec.get("expect_latest")
    if expect_latest and latest > expect_latest:
        hf("future_bar", latest=latest, expect_latest=expect_latest)

    # ── operation 開關：daily_incremental 要求最新==最近已完成交易日 ──
    if operation == "daily_incremental" and expect_latest and latest != expect_latest:
        # 落後才報；領先已在 future_bar 抓
        if latest < expect_latest:
            hf("stale_latest", latest=latest, expect_latest=expect_latest)

    # ── D period 連續性（對 market_calendar；historical_backfill 深歷史降級）──
    if period == "D" and holidays and operation != "synthesis":
        import datetime
        lo_bound, hi_bound = oldest, latest
        if spec.get("date_range"):
            lo_bound, hi_bound = spec["date_range"]
        # 深歷史（<2024，日曆未涵蓋）降級：只驗 2024 起
        if operation == "historical_backfill" and lo_bound < "2024-01-01":
            lo_bound = "2024-01-01"
        present = {b.get("t", "")[:10] for b in bars}
        missing = []
        d = datetime.date.fromisoformat(hi_bound)
        d_lo = datetime.date.fromisoformat(lo_bound)
        while d >= d_lo:
            ds = d.isoformat()
            if d.weekday() < 5 and ds not in holidays and ds not in present:
                missing.append(ds)
            d -= datetime.timedelta(days=1)
        if missing:
            # 跨票系統性缺（單日全票缺）由 audit 層降級為日曆 warning；純核心據實報 hard_fail
            hf("calendar_gap", n=len(missing), sample=missing[:8])

    # ── 相鄰大跳幅 → warning（可能分割，不硬擋）──
    for i in range(len(bars) - 1):
        a, b2 = bars[i].get("c"), bars[i + 1].get("c")
        try:
            a, b2 = float(a), float(b2)
        except (TypeError, ValueError):
            continue
        if b2 and b2 != 0 and abs(a - b2) / abs(b2) > BIG_JUMP_WARN_PCT:
            wn("big_jump", newer=bars[i].get("t", "")[:10], older=bars[i + 1].get("t", "")[:10],
               pct=round(abs(a - b2) / abs(b2), 4))

    # ── identity_break（SPCX 式 ticker 回收重用）──
    list_date = spec.get("list_date")
    if list_date and len(list_date) >= 10:
        pre = [b for b in bars if b.get("t", "")[:10] < list_date]
        if pre:
            jump, vol_ratio = _seam_metrics(bars, list_date)
            severe = (jump > IDENTITY_SEVERE_JUMP_PCT
                      or vol_ratio > IDENTITY_SEVERE_VOL_RATIO
                      or vol_ratio < 1.0 / IDENTITY_SEVERE_VOL_RATIO)
            info = dict(list_date=list_date, n_pre=len(pre),
                        jump_pct=round(jump, 4),
                        vol_ratio=(round(vol_ratio, 2) if vol_ratio != float("inf") else "inf"),
                        action=("trim_to_list_date" if severe else "keep"))
            if severe:
                hf("identity_break_severe", **info)
            else:
                wn("identity_break_mild", **info)
                # 灰色地帶（近門檻、規則說不準）→ 標記交 runner 層問免費模型判疑（純核心不呼叫 AI）
                gray = (IDENTITY_GRAY_JUMP_PCT < jump <= IDENTITY_SEVERE_JUMP_PCT
                        or IDENTITY_GRAY_VOL_RATIO < vol_ratio <= IDENTITY_SEVERE_VOL_RATIO
                        or 1.0 / IDENTITY_SEVERE_VOL_RATIO <= vol_ratio < 1.0 / IDENTITY_GRAY_VOL_RATIO)
                if gray:
                    wn("needs_adjudication", **info)

    return {"ok": not hard_fails, "ticker": ticker,
            "latest": latest, "oldest": oldest, "n": len(bars),
            "hard_fails": hard_fails, "warnings": warnings}
