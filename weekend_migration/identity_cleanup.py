# -*- coding: utf-8 -*-
"""
identity_cleanup.py — SPCX 式 ticker 回收重用清理（migration step，§identity_break）

對 bar_validator 判為 **severe** 的票（現有 Bars_D 有 list_date 前的舊實體、且接縫價量斷裂大）：
  1) 封存舊實體 bars（t < list_date）→ Bars_{tk}_prelisting（不刪，可回溯）
  2) TD 重抓乾淨新實體（TD 只有 list_date 起的資料）→ 過 bar_validator 閘門 → REPLACE Bars_{tk} D/H
  3) W 從乾淨新 D resample → REPLACE

mild（價格連續的重組/分拆，如 LAC/QBTS）**不在此清理**：保留現狀，只由 audit 記錄。

- 嚴重度由 bar_validator 規則判（不硬編 ticker）。
- 預設 dry-run；--write 才真的封存 + 覆蓋。
用法：
  MIG_ENV_FILE=... python identity_cleanup.py                 # dry-run 全庫掃 severe
  MIG_ENV_FILE=... python identity_cleanup.py --write
  MIG_ENV_FILE=... python identity_cleanup.py --tickers SPCX,LUNR --write
"""
import os, sys, io, argparse, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc
import bar_validator as bv

BARS_LIMIT_D = 1100
BARS_LIMIT_H = 5000
FETCH_D = 1200
FETCH_H = 1400


def _gate(bars, period, list_date, expect_latest, cal):
    """過 bar_validator 閘門：回 (ok, verdict, blocking)。乾淨新實體不該再有 identity_break。"""
    spec = {"period": period, "operation": "historical_backfill",
            "list_date": list_date, "expect_latest": expect_latest, "calendar": cal}
    v = bv.validate(bars, spec)
    blocking = bv.blocking_fails(v)
    return (not blocking, v, blocking)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--tickers", default=None, help="逗號分隔；留空=全庫掃 severe")
    ap.add_argument("--expect-latest", default="2026-08-07")
    args = ap.parse_args()

    cli, sd = mc.get_stock_db()
    cal = mc.load_calendar(sd)
    ident = mc.load_identity_map(sd)
    universe = mc.get_all_tickers(sd)
    scope = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else universe
    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"[identity_cleanup] {mode} | 掃 {len(scope)} 支找 severe | expect_latest={args.expect_latest}")

    # 找 severe
    severe = []
    for tk in scope:
        ld = ident.get(tk.upper())
        if not ld:
            continue
        existing = (sd[f"Bars_{tk}"].find_one({"period": "D"}) or {}).get("bars", [])
        sev = mc.identity_severity(existing, ld, cal)
        if sev == "severe":
            severe.append((tk, ld))
        elif sev == "gray":
            # 規則近門檻說不準 → 問 AC 免費模型（數字無法解決的小問題）
            jump, vol_ratio = bv._seam_metrics(existing, ld)
            id_doc = sd["Ticker_Identity"].find_one({"ticker": tk}) or {}
            case = {"ticker": tk, "company_name": id_doc.get("company_name", ""),
                    "list_date": ld, "jump_pct": round(jump, 4),
                    "vol_ratio": (round(vol_ratio, 2) if vol_ratio != float("inf") else "inf"),
                    "pre_sample": [{"t": b["t"][:10], "c": b["c"], "v": b.get("v", 0)}
                                   for b in [x for x in existing if x["t"][:10] < ld][:3]],
                    "post_sample": [{"t": b["t"][:10], "c": b["c"], "v": b.get("v", 0)}
                                    for b in [x for x in existing if x["t"][:10] >= ld][-3:]]}
            verdict = mc.ai_adjudicate(case)
            print(f"   [gray] {tk}（jump={case['jump_pct']} vol={case['vol_ratio']}）→ AI 判疑: {verdict or '不可用→保守保留'}")
            if verdict == "reuse":
                severe.append((tk, ld))
            # 'keep' / None → 當 mild 保留，不清理（保守，不誤刪）
    print(f"[identity_cleanup] 判定 severe（含 AI 判 reuse）：{[t for t, _ in severe]}\n")

    td = mc.TDClient()
    done = []
    outcomes = []
    for tk, ld in severe:
        print(f"── {tk}（list_date={ld}）──")
        # 1) 封存舊實體 D + H
        for period in ("D", "H"):
            n_arch = mc.archive_prelisting(sd, tk, period, ld, args.write)
            print(f"   封存 {period} 舊實體 {n_arch} 根 → Bars_{tk}_prelisting{'（dry-run 未寫）' if not args.write else ''}")

        # 2) 乾淨新實體 D
        res = td.fetch([tk], "1day", outputsize=FETCH_D)
        r = res.get(tk, {})
        if r.get("status") == "ok" and r.get("values"):
            d_new = mc.td_values_to_bars(r["values"], "D")
            # trim_to_list_date：只留 list_date 起的新實體（TD 有時多給 1 根上市前一日；排除 forming）
            d_new = [b for b in d_new if ld <= b["t"][:10] <= args.expect_latest][:BARS_LIMIT_D]
            ok, v, blk = _gate(d_new, "D", ld, args.expect_latest, cal)
            if ok and d_new:
                if args.write:
                    sd[f"Bars_{tk}"].update_one({"ticker": tk, "period": "D"},
                        {"$set": {"ticker": tk, "period": "D", "bars": d_new,
                                  "updated_at": datetime.datetime.now(datetime.timezone.utc)}}, upsert=True)
                print(f"   ✅ D 乾淨新實體 {len(d_new)} 根 [{d_new[-1]['t'][:10]}~{d_new[0]['t'][:10]}] → {'replaced' if args.write else 'dry-run'}")
            else:
                print(f"   ❌ D 閘門擋下（{[x['code'] for x in blk]}），保留現有不覆蓋")
                d_new = None
        else:
            print(f"   ❌ D TD 無資料 → 保留現有不覆蓋")
            d_new = None

        # 3) 乾淨新實體 H
        res = td.fetch([tk], "30min", outputsize=FETCH_H)
        r = res.get(tk, {})
        if r.get("status") == "ok" and r.get("values"):
            h_new = mc.td_values_to_bars(r["values"], "H")
            h_new = [b for b in h_new if ld <= b["t"][:10] <= args.expect_latest][:BARS_LIMIT_H]
            ok, v, blk = _gate(h_new, "H", ld, args.expect_latest, cal)
            if ok and h_new:
                if args.write:
                    sd[f"Bars_{tk}"].update_one({"ticker": tk, "period": "H"},
                        {"$set": {"ticker": tk, "period": "H", "bars": h_new,
                                  "updated_at": datetime.datetime.now(datetime.timezone.utc)}}, upsert=True)
                print(f"   ✅ H 乾淨新實體 {len(h_new)} 根 → {'replaced' if args.write else 'dry-run'}")
            else:
                print(f"   ❌ H 閘門擋下（{[x['code'] for x in blk]}），保留現有不覆蓋")
        else:
            print(f"   ❌ H TD 無資料 → 保留現有不覆蓋")

        # 4) W 從乾淨新 D resample
        if d_new:
            from mig_common import TDClient  # noqa
            # 直接用 w_resample 邏輯（週一戳）
            w_new = _resample_w(d_new)[:600]
            if w_new and args.write:
                sd[f"Bars_{tk}"].update_one({"ticker": tk, "period": "W"},
                    {"$set": {"ticker": tk, "period": "W", "bars": w_new,
                              "updated_at": datetime.datetime.now(datetime.timezone.utc)}}, upsert=True)
            print(f"   {'✅' if w_new else '⚠️'} W 從乾淨 D resample {len(w_new)} 週 → {'replaced' if args.write else 'dry-run'}")
        done.append(tk)
        outcomes.append({"ticker": tk, "action": "identity_cleaned", "list_date": ld,
                         "latest": (d_new[0]["t"][:10] if d_new else None), "codes": ["identity_break_severe"]})

    print(f"\n[identity_cleanup] {mode} 完成 | 處理 severe {len(done)} 支：{done}")
    mc.write_validation_log(sd, context="identity_cleanup", period="D", operation="historical_backfill",
                            scope={"scanned": len(scope), "severe": [t for t, _ in severe]},
                            outcomes=outcomes, write=args.write)
    if not args.write:
        print("[identity_cleanup] （dry-run，未封存/未覆蓋任何資料）")
    cli.close()


def _resample_w(d_bars):
    """D（新在前）→ W（週一戳，週OHLC）。與 w_resample.py 同邏輯。"""
    import datetime as _dt
    from collections import OrderedDict
    weeks = OrderedDict()
    for b in reversed(d_bars):  # 舊到新
        day = _dt.date.fromisoformat(b["t"][:10])
        monday = (day - _dt.timedelta(days=day.weekday())).isoformat()
        g = weeks.setdefault(monday, {"o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": 0.0})
        g["h"] = max(g["h"], b["h"]); g["l"] = min(g["l"], b["l"])
        g["c"] = b["c"]; g["v"] += b.get("v", 0)
    out = [{"t": f"{mon} 00:00:00", "o": g["o"], "h": g["h"], "l": g["l"], "c": g["c"], "v": g["v"]}
           for mon, g in weeks.items()]
    out.sort(key=lambda x: x["t"], reverse=True)
    return out


if __name__ == "__main__":
    main()
