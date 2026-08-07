# -*- coding: utf-8 -*-
"""
w_resample.py — 從（新）Bars_D resample 出週線 → 覆蓋 Bars_W（§14.4 step 5）

- 週線 = 該週日線聚合：open=週首開、high=週高、low=週低、close=週末收、volume=週總量。
- 時間戳 = 該週**週一**（date - weekday 天），t='YYYY-MM-DD 00:00:00'。
  ⚠️ DANGER_ZONES「W期時間戳」講的「-timedelta(days=weekday()) 會提早一週」是針對**處理
     Polygon 週日戳**的情境；這裡是**從日線 resample**，對週一~週五的日期取 -weekday() 得到的
     正是本週週一，正確。**但必驗**：本腳本 dry-run 會比對現有 Bars_W 的週一戳是否一致。
- 只用**已收盤**的完整週（跳過當週未收盤：最新一週若不含週五 D bar，可選擇保留部分/剔除——
  預設保留（週末跑時當週已收完）；週中測試會看到部分週）。
- 預設 dry-run，--write 才寫。
"""
import os, sys, io, argparse, datetime
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc

BARS_LIMIT_W = 1500        # 對齊 DC config.py:43


def monday_of(date_str):
    d = datetime.date.fromisoformat(date_str[:10])
    mon = d - datetime.timedelta(days=d.weekday())
    return mon.isoformat()


def resample_d_to_w(d_bars):
    """d_bars 新在前 → w_bars 新在前。"""
    weeks = defaultdict(list)
    for b in d_bars:
        weeks[monday_of(b["t"])].append(b)
    out = []
    for mon, bars in weeks.items():
        bars = sorted(bars, key=lambda x: x["t"])   # 週內舊→新
        out.append({
            "t": f"{mon} 00:00:00",
            "o": bars[0]["o"],
            "h": max(x["h"] for x in bars),
            "l": min(x["l"] for x in bars),
            "c": bars[-1]["c"],
            "v": sum(x.get("v") or 0 for x in bars),
        })
    out.sort(key=lambda x: x["t"], reverse=True)     # 新在前
    return out[:BARS_LIMIT_W]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
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
    print(f"[w_resample] {mode} | {len(tickers)} 支 | cap={BARS_LIMIT_W}")

    ok = fail = mismatch = 0
    for tk in tickers:
        d_doc = sd[f"Bars_{tk}"].find_one({"period": "D"}) or {}
        d_bars = d_doc.get("bars", [])
        if len(d_bars) < 20:
            fail += 1
            print(f"  ❌ {tk}: D bar 不足 → 跳過")
            continue
        w_bars = resample_d_to_w(d_bars)
        # 驗證：跟現有 Bars_W 的週一戳比對（抽最新 5 週）
        old_w = (sd[f"Bars_{tk}"].find_one({"period": "W"}) or {}).get("bars", [])
        old_t = {b["t"][:10] for b in old_w[:5]}
        new_t = {b["t"][:10] for b in w_bars[:5]}
        common = old_t & new_t
        mm = ""
        if old_w and len(common) < min(3, len(new_t)):
            mismatch += 1
            mm = f"  ⚠️週一戳對不上(舊{sorted(old_t)} vs 新{sorted(new_t)})"
        if args.write:
            sd[f"Bars_{tk}"].update_one(
                {"ticker": tk, "period": "W"},
                {"$set": {"ticker": tk, "period": "W", "bars": w_bars,
                          "updated_at": datetime.datetime.now(datetime.timezone.utc)}},
                upsert=True)
        ok += 1
        print(f"  ✅ {tk}: {len(w_bars)}週 [{w_bars[-1]['t'][:10]}~{w_bars[0]['t'][:10]}] "
              f"最新週close={w_bars[0]['c']:.2f}{mm}")
    print(f"\n[w_resample] {mode} 完成 | 成功 {ok} | 失敗 {fail} | 週一戳對不上 {mismatch}")
    if not args.write:
        print("[w_resample] （dry-run，未寫任何資料）")
    cli.close()


if __name__ == "__main__":
    main()
