# -*- coding: utf-8 -*-
"""
d_backfill.py — TD 日線連續調整 backfill → 覆蓋 Bars_D（§14.4 step 1）

- TD 抓 342 票 1day（連續回溯調整口徑，修分割斷層），覆蓋現有 Bars_D。
- **REPLACE**（非 merge）：TD 是權威；但 TD 失敗/空的 ticker **跳過、不清空**現有資料。
- 排除 forming（未收盤）bar：--cutoff-date YYYY-MM-DD（保留 <= cutoff 的 bar）。週末跑市場關、
  可不設（TD 最新就是週五 settled）；平日測試務必設。
- 預設 **dry-run**（只算、印摘要、不寫）；加 --write 才真的覆蓋。

用法：
  MIG_ENV_FILE=... python d_backfill.py                       # dry-run 全部
  MIG_ENV_FILE=... python d_backfill.py --limit 5             # dry-run 前5支
  MIG_ENV_FILE=... python d_backfill.py --write --cutoff-date 2026-08-05
"""
import os, sys, io, argparse, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc

BARS_LIMIT_D = 1100        # 對齊 DC config.py:42
FETCH_OUTPUTSIZE = 1200    # 略多於 cap；TD 連續調整與窗口大小無關，1200 已足填滿 1100
BATCH = 8                  # 每次 TD 請求票數（沿用 intraday.py 慣例）


def _split_cliff(bars):
    """回傳最大相鄰日 close 跳變比例（偵測分割斷層；bars 新在前）。"""
    worst = 0.0
    for i in range(len(bars) - 1):
        a, b = bars[i].get("c"), bars[i + 1].get("c")
        if a and b and b != 0:
            r = abs(a - b) / abs(b)
            worst = max(worst, r)
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真的覆蓋 Mongo（預設 dry-run）")
    ap.add_argument("--cutoff-date", default=None, help="只保留 <= 此日期的 bar（排除 forming）")
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 支（測試）")
    ap.add_argument("--tickers", default=None, help="逗號分隔指定票（測試，覆蓋 --limit）")
    args = ap.parse_args()

    cli, sd = mc.get_stock_db()
    tickers = mc.get_all_tickers(sd)
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.limit:
        tickers = tickers[:args.limit]
    mode = "WRITE(覆蓋)" if args.write else "DRY-RUN(只算不寫)"
    print(f"[d_backfill] {mode} | {len(tickers)} 支 | cutoff={args.cutoff_date or '(無,週末安全)'} | cap={BARS_LIMIT_D}")

    td = mc.TDClient()
    ok = fail = skipped = fixed = 0
    for batch in mc.batched(tickers, BATCH):
        res = td.fetch(batch, "1day", outputsize=FETCH_OUTPUTSIZE)
        for tk in batch:
            r = res.get(tk, {})
            if r.get("status") != "ok" or not r.get("values"):
                fail += 1
                print(f"  ❌ {tk}: TD 無資料（status={r.get('status')} msg={r.get('message','')[:60]}）→ 跳過不清空")
                continue
            bars = mc.td_values_to_bars(r["values"], "D")   # 新在前
            if args.cutoff_date:
                bars = [b for b in bars if b["t"][:10] <= args.cutoff_date]
            bars = bars[:BARS_LIMIT_D]
            if len(bars) < 20:
                fail += 1
                print(f"  ❌ {tk}: 解析後僅 {len(bars)} 根 → 跳過")
                continue
            # 分割修復偵測：舊 vs 新的最大跳變
            old_doc = sd[f"Bars_{tk}"].find_one({"period": "D"}) or {}
            old_bars = old_doc.get("bars", [])
            old_cliff = _split_cliff(old_bars)
            new_cliff = _split_cliff(bars)
            fix_note = ""
            if old_cliff > 0.5 and new_cliff < 0.5:
                fixed += 1
                fix_note = f"  🔧修分割斷層({old_cliff*100:.0f}%→{new_cliff*100:.0f}%)"
            if args.write:
                sd[f"Bars_{tk}"].update_one(
                    {"ticker": tk, "period": "D"},
                    {"$set": {"ticker": tk, "period": "D", "bars": bars,
                              "updated_at": datetime.datetime.now(datetime.timezone.utc)}},
                    upsert=True)
            ok += 1
            print(f"  ✅ {tk}: {len(bars)}根 [{bars[-1]['t'][:10]}~{bars[0]['t'][:10]}] "
                  f"最新close={bars[0]['c']:.2f}{fix_note}")
    print(f"\n[d_backfill] {mode} 完成 | 成功 {ok} | 失敗/跳過 {fail} | 偵測修分割 {fixed}")
    if not args.write:
        print("[d_backfill] （這是 dry-run，未寫任何資料；確認無誤後加 --write）")
    cli.close()


if __name__ == "__main__":
    main()
