# -*- coding: utf-8 -*-
"""
h_backfill.py — TD 30分鐘 backfill → 覆蓋 Bars_H（§14.4 step 2）

- TD 抓 342 票 30min（深度對齊現有 ~5 個月），RTH 過濾（09:30~15:30），覆蓋 Bars_H。
- 修 lag（Polygon 慢一天）+ 分割斷層。REPLACE，TD 失敗跳過不清空。
- forming 排除同 d_backfill（--cutoff-date；週末跑安全）。
- 預設 dry-run，加 --write 才真寫。

⚠️ Bars_H 是「30分鐘 RTH」——td_values_to_bars(period='H') 已做 RTH_SLOTS 過濾
   （治退役 LC 曾存非交易時段 bar 的教訓，見 DANGER_ZONES 2026-07-25）。
"""
import os, sys, io, argparse, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc
import bar_validator as bv

BARS_LIMIT_H = 5000        # 對齊 DC config.py:41（cap，非目標深度）
FETCH_OUTPUTSIZE = 1400    # 維持現有深度 ~5 個月 RTH（13根/日×~105交易日≈1365，用戶 2026-08-05 拍板不加深）
BATCH = 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--cutoff-date", default=None, help="只保留 <= 此日期的 bar（排除 forming）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tickers", default=None)
    args = ap.parse_args()

    cli, sd = mc.get_stock_db()
    tickers = mc.get_all_tickers(sd)
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.limit:
        tickers = tickers[:args.limit]
    mode = "WRITE(覆蓋)" if args.write else "DRY-RUN(只算不寫)"

    # identity 票排除（同 d_backfill）：severe 交 identity_cleanup、mild 保留現狀
    cal = mc.load_calendar(sd)
    ident = mc.load_identity_map(sd)
    expect_latest = args.cutoff_date or mc.completed_trading_date(cal)
    id_skip = []
    for tk in tickers:
        ld = ident.get(tk.upper())
        if not ld:
            continue
        existing = (sd[f"Bars_{tk}"].find_one({"period": "D"}) or {}).get("bars", [])
        sev = mc.identity_severity(existing, ld, cal)
        if sev:
            id_skip.append((tk, sev))
    if id_skip:
        skip_set = {t for t, _ in id_skip}
        tickers = [t for t in tickers if t not in skip_set]
        print(f"[h_backfill] 排除 {len(id_skip)} 支 identity 票（severe→identity_cleanup、mild→保留現狀）: {id_skip}")

    print(f"[h_backfill] {mode} | {len(tickers)} 支 | cutoff={args.cutoff_date or '(無)'} | cap={BARS_LIMIT_H}")

    td = mc.TDClient()
    ok = fail = 0
    outcomes = [{"ticker": t, "action": "kept" if s == "mild" else "archived_elsewhere"}
                for t, s in id_skip]
    for batch in mc.batched(tickers, BATCH):
        res = td.fetch(batch, "30min", outputsize=FETCH_OUTPUTSIZE)
        for tk in batch:
            r = res.get(tk, {})
            if r.get("status") != "ok" or not r.get("values"):
                fail += 1
                outcomes.append({"ticker": tk, "action": "skipped", "codes": ["td_no_data"]})
                print(f"  ❌ {tk}: TD 無資料 → 跳過不清空")
                continue
            bars = mc.td_values_to_bars(r["values"], "H")   # 新在前、已 RTH 過濾
            if args.cutoff_date:
                bars = [b for b in bars if b["t"][:10] <= args.cutoff_date]
            bars = bars[:BARS_LIMIT_H]
            if len(bars) < 50:
                fail += 1
                outcomes.append({"ticker": tk, "action": "skipped", "codes": ["too_few_bars"]})
                print(f"  ❌ {tk}: RTH 過濾後僅 {len(bars)} 根 → 跳過")
                continue
            # bar_validator 寫入閘門（同 d_backfill）
            gv = bv.validate(bars, {"period": "H", "operation": "historical_backfill",
                                    "expect_latest": expect_latest, "calendar": cal,
                                    "list_date": ident.get(tk.upper())})
            blk = bv.blocking_fails(gv)
            if blk:
                fail += 1
                outcomes.append({"ticker": tk, "action": "blocked", "codes": [x["code"] for x in blk]})
                print(f"  ❌ {tk}: 閘門擋下 {[x['code'] for x in blk]} → 跳過不覆蓋")
                continue
            # 抽查：最新 bar 時間該落在 RTH、且日期覆蓋數
            days = len({b['t'][:10] for b in bars})
            if args.write:
                sd[f"Bars_{tk}"].update_one(
                    {"ticker": tk, "period": "H"},
                    {"$set": {"ticker": tk, "period": "H", "bars": bars,
                              "updated_at": datetime.datetime.now(datetime.timezone.utc)}},
                    upsert=True)
            ok += 1
            outcomes.append({"ticker": tk, "action": "replaced", "latest": bars[0]["t"][:10]})
            print(f"  ✅ {tk}: {len(bars)}根/{days}天 [{bars[-1]['t']}~{bars[0]['t']}] 最新close={bars[0]['c']:.2f}")
    print(f"\n[h_backfill] {mode} 完成 | 成功 {ok} | 失敗/跳過 {fail}")
    mc.write_validation_log(sd, context="migration_h", period="H", operation="historical_backfill",
                            scope={"n_tickers": len(tickers) + len(id_skip), "expect_latest": expect_latest,
                                   "ok": ok, "fail": fail},
                            outcomes=outcomes, write=args.write)
    if not args.write:
        print("[h_backfill] （dry-run，未寫任何資料）")
    cli.close()


if __name__ == "__main__":
    main()
