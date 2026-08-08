# -*- coding: utf-8 -*-
"""
run_bar_audit.py — bar_validator 全庫 audit runner（送檢方/runner 層）

- 讀 market_calendar（日期權威）+ Ticker_Identity（身份權威，list_date）→ 套 bar_validator 全庫掃。
- 純核心零 IO；本 runner 負責取資料、組 spec、彙總、（可選）寫 Validation_Log。
- 預設唯讀（不寫 log）；--write-log 才寫 StockData.Validation_Log。
- 設計：handoff_task_data_validator.md。

用法：
  MIG_ENV_FILE=... python run_bar_audit.py                 # 唯讀，印彙總 + 該寫的 log doc
  MIG_ENV_FILE=... python run_bar_audit.py --period D
  MIG_ENV_FILE=... python run_bar_audit.py --write-log     # 另寫 Validation_Log
"""
import os, sys, io, argparse, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc
import bar_validator as bv


def load_calendar(sd):
    doc = sd["Configs"].find_one({"type": "market_calendar"}) or {}
    holidays = set(doc.get("holidays", []))
    half = set(h.get("date") for h in doc.get("half_days", []) if isinstance(h, dict))
    return {"holidays": holidays, "half_days": half}


def load_identity(sd):
    out = {}
    for d in sd["Ticker_Identity"].find({}):
        tk = d.get("ticker") or d.get("_id")
        if isinstance(tk, str) and d.get("list_date"):
            out[tk.upper()] = d["list_date"]
    return out


def completed_trading_date(cal, today=None):
    """最近「已完成」交易日：保守從昨天起往前找第一個平日-非假日（不認今天的 forming bar）。
    週末跑不受影響（Sat→Fri）；平日盤後想認今天需另傳 --expect-latest。"""
    today = today or datetime.date.today()
    d = today - datetime.timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5 and d.isoformat() not in cal["holidays"]:
            return d.isoformat()
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="D", help="D/W/H")
    ap.add_argument("--tickers", default=None)
    ap.add_argument("--expect-latest", default=None, help="最近已完成交易日；留空自動算")
    ap.add_argument("--range-start", default="2026-06-01", help="連續性檢查起（避開深歷史/分割噪音）")
    ap.add_argument("--write-log", action="store_true")
    args = ap.parse_args()

    cli, sd = mc.get_stock_db()
    cal = load_calendar(sd)
    ident = load_identity(sd)
    tickers = mc.get_all_tickers(sd)
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    expect_latest = args.expect_latest or completed_trading_date(cal)
    period = args.period

    print(f"[audit] period={period} | {len(tickers)} 支 | 最近已完成交易日={expect_latest} "
          f"| calendar holidays={len(cal['holidays'])} | identity list_date={len(ident)}")

    from collections import Counter
    code_count = Counter()
    problems = []
    n_ok = 0
    for tk in tickers:
        doc = sd[f"Bars_{tk}"].find_one({"period": period})
        bars = (doc or {}).get("bars", [])
        spec = {"ticker": tk, "period": period, "operation": "daily_incremental",
                "expect_latest": expect_latest, "calendar": cal, "list_date": ident.get(tk.upper()),
                "date_range": (args.range_start, expect_latest)}
        v = bv.validate(bars, spec)
        for x in v["hard_fails"]:
            code_count["HF:" + x["code"]] += 1
        for x in v["warnings"]:
            code_count["WN:" + x["code"]] += 1
        if v["ok"] and not v["warnings"]:
            n_ok += 1
        if v["hard_fails"] or any(w["code"].startswith("identity") for w in v["warnings"]):
            problems.append((tk, v))

    print(f"\n=== 彙總（{len(tickers)} 支）===")
    print(f"  完全乾淨（無 hard_fail 無 warning）: {n_ok}")
    for code, cnt in sorted(code_count.items(), key=lambda x: -x[1]):
        print(f"  {code}: {cnt}")

    # identity_break 專節（用戶要審核的重點）
    print(f"\n=== identity_break 專節 ===")
    for tag, key in [("SEVERE(hard_fail→建議清)", "identity_break_severe"),
                     ("MILD(warning→建議保留)", "identity_break_mild")]:
        rows = []
        for tk, v in problems:
            for x in (v["hard_fails"] + v["warnings"]):
                if x["code"] == key:
                    rows.append((tk, x, v))
        print(f"  【{tag}】{len(rows)} 支")
        for tk, x, v in sorted(rows):
            cn = ""
            print(f"    {tk}: list_date={x['list_date']} 舊實體{x['n_pre']}根 "
                  f"跳{x['jump_pct']*100:.0f}% 量比{x['vol_ratio']} → {x['action']}")

    # 落後（＝遷移目標）與其他 hard_fail 摘要
    stale = sorted(tk for tk, v in problems if any(h["code"] == "stale_latest" for h in v["hard_fails"]))
    print(f"\n=== 落後最新（stale，＝遷移目標）: {len(stale)} 支 ===")
    print("  " + ",".join(stale))
    other_hf = [(tk, [h["code"] for h in v["hard_fails"] if h["code"] not in ("stale_latest",)])
                for tk, v in problems]
    other_hf = [(tk, cs) for tk, cs in other_hf if cs]
    print(f"\n=== 其他 hard_fail（非 stale）: {len(other_hf)} 支 ===")
    for tk, cs in sorted(other_hf):
        print(f"    {tk}: {cs}")

    # 該寫進 Validation_Log 的彙總 doc
    log_doc = {
        "run_at": datetime.datetime.now(datetime.timezone.utc),
        "context": "adhoc_migration_audit_20260808",
        "period": period, "operation": "daily_incremental",
        "scope": {"n_tickers": len(tickers), "expect_latest": expect_latest},
        "summary": {"clean": n_ok, "codes": dict(code_count)},
        "ok": not any(k.startswith("HF:") for k in code_count),
        "problems_capped": [
            {"ticker": tk, "hard_fails": [h["code"] for h in v["hard_fails"]],
             "warnings": [w["code"] for w in v["warnings"]]}
            for tk, v in problems[:80]
        ],
    }
    if args.write_log:
        try:
            sd["Validation_Log"].insert_one(dict(log_doc))
            print("\n[audit] ✅ 已寫 Validation_Log")
        except Exception as e:
            print(f"\n[audit] ⚠️ 寫 Validation_Log 失敗（可能唯讀 URI）: {mc.scrub(e)}")
            print(f"[audit] 該寫的 log doc summary: {log_doc['summary']}")
    else:
        print(f"\n[audit] （唯讀，未寫 log）該寫的 log summary: {log_doc['summary']}")

    cli.close()


if __name__ == "__main__":
    main()
