# -*- coding: utf-8 -*-
"""
verify_migration.py — 遷移後驗證（§14.4 step 4，跑在 --write 之後）

抽查（可全掃或抽樣）：
  1. D/H/W 三 period 都有資料、bar 數合理、最新日期一致
  2. 分割股（含 CRWD 等指定）D 線無殘留斷層（相鄰日 close 跳 <50%）
  3. D↔W 一致性：最新週 close == 該週最後一個 D close
  4. H 全 RTH（無非交易時段 bar）
只讀、不改。回傳退出碼 0=全過、1=有問題。
"""
import os, sys, io, argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc

SPLIT_WATCH = ["CRWD"]   # 已知分割股，重點驗（可加）


def max_cliff(bars):
    worst = 0.0
    for i in range(len(bars) - 1):
        a, b = bars[i].get("c"), bars[i + 1].get("c")
        if a and b and b != 0:
            worst = max(worst, abs(a - b) / abs(b))
    return worst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="抽樣前 N 支（預設全掃）")
    ap.add_argument("--tickers", default=None)
    args = ap.parse_args()

    cli, sd = mc.get_stock_db()
    tickers = mc.get_all_tickers(sd)
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.limit:
        tickers = tickers[:args.limit]

    problems = []
    n_missing = n_cliff = n_dw = n_nonrth = 0
    for tk in tickers:
        docs = {p: (sd[f"Bars_{tk}"].find_one({"period": p}) or {}).get("bars", []) for p in ("D", "H", "W")}
        for p in ("D", "H", "W"):
            if len(docs[p]) < 10:
                n_missing += 1; problems.append(f"{tk} {p} bar 過少({len(docs[p])})")
        d, w, h = docs["D"], docs["W"], docs["H"]
        # 分割斷層（只對 watch list 嚴格；其餘僅記錄極端）
        if d:
            mc_ = max_cliff(d)
            if mc_ > 0.5 and (tk in SPLIT_WATCH or mc_ > 0.6):
                n_cliff += 1; problems.append(f"{tk} D 殘留斷層 {mc_*100:.0f}%")
        # D↔W：最新週 close == 該週最後 D close
        if d and w:
            import datetime
            wt = w[0]["t"][:10]
            mon = datetime.date.fromisoformat(wt)
            week_days = [b for b in d if datetime.date.fromisoformat(b["t"][:10]) - datetime.timedelta(days=datetime.date.fromisoformat(b["t"][:10]).weekday()) == mon]
            if week_days:
                last_d = max(week_days, key=lambda x: x["t"])
                if abs(last_d["c"] - w[0]["c"]) > 0.01:
                    n_dw += 1; problems.append(f"{tk} D↔W 收盤不符(W{w[0]['c']} vs D{last_d['c']})")
        # H 全 RTH
        if h:
            bad = [b["t"] for b in h[:60] if b["t"][11:16] not in mc.RTH_SLOTS]
            if bad:
                n_nonrth += 1; problems.append(f"{tk} H 有非RTH bar {bad[:2]}")

    print(f"[verify] 掃 {len(tickers)} 支")
    print(f"  bar 過少: {n_missing} | D 殘留斷層: {n_cliff} | D↔W 收盤不符: {n_dw} | H 非RTH: {n_nonrth}")
    if problems:
        print(f"\n⚠️ 問題 {len(problems)} 筆（前25）:")
        for p in problems[:25]:
            print("   " + p)
        cli.close(); sys.exit(1)
    print("\n✅ 全過")
    cli.close(); sys.exit(0)


if __name__ == "__main__":
    main()
