# -*- coding: utf-8 -*-
"""
verify_migration.py — 遷移後驗證（§14.4 step 4，跑在 --write 之後）

抽查（可全掃或抽樣）。分兩級，避免誤殺真實走勢（見 DANGER_ZONES §12）：
  【硬失敗 → 退出碼 1】真結構問題：
    - D↔W 一致性：最新週 close == 該週最後一個 D close
    - H 全 RTH（無非交易時段 bar）
    - 已知分割股（SPLIT_WATCH，如 CRWD）D 線殘留斷層＝TD 未調乾淨
  【警告 → 只印不擋】非失敗：
    - 一般票 D 大跳幅（真實暴漲/de-SPAC/分割，TD 連續調整刻意保留）
    - bar 少（新上市如 SPCX 才 9 週；寫入時已有 bar_validator 閘門守）
只讀、不改。退出碼 0=無硬失敗、1=有硬失敗。
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

    # 硬失敗（真結構問題 → 退出碼 1）vs 警告（真實暴漲/分割/新上市，只印不擋，見 DANGER_ZONES §12）
    hard_fails = []
    warnings = []
    n_missing = n_cliff = n_dw = n_nonrth = 0
    for tk in tickers:
        docs = {p: (sd[f"Bars_{tk}"].find_one({"period": p}) or {}).get("bars", []) for p in ("D", "H", "W")}
        for p in ("D", "H", "W"):
            if len(docs[p]) < 10:
                # bar 少多為新上市（如 SPCX 才 9 週）；且寫入時已有 bar_validator 閘門守（≥20 D/≥50 H）
                # → 只警告不擋，避免誤殺新上市
                n_missing += 1; warnings.append(f"{tk} {p} bar 少({len(docs[p])})（新上市或已由寫入閘門守）")
        d, w, h = docs["D"], docs["W"], docs["H"]
        # 相鄰大跳幅：只有「已知分割股仍有殘留斷層」才是真問題（TD 未調乾淨）；
        # 其餘票的大跳幅＝真實暴漲/de-SPAC/分割，TD 連續調整刻意保留，只警告不擋（見 DANGER_ZONES §12）
        if d:
            mc_ = max_cliff(d)
            if mc_ > 0.5:
                if tk in SPLIT_WATCH:
                    n_cliff += 1; hard_fails.append(f"{tk} D 分割未調乾淨 {mc_*100:.0f}%（SPLIT_WATCH）")
                else:
                    warnings.append(f"{tk} D 大跳 {mc_*100:.0f}%（真實走勢/分割，非失敗）")
        # D↔W：最新週 close == 該週最後 D close（真一致性 → 硬失敗）
        if d and w:
            import datetime
            wt = w[0]["t"][:10]
            mon = datetime.date.fromisoformat(wt)
            week_days = [b for b in d if datetime.date.fromisoformat(b["t"][:10]) - datetime.timedelta(days=datetime.date.fromisoformat(b["t"][:10]).weekday()) == mon]
            if week_days:
                last_d = max(week_days, key=lambda x: x["t"])
                if abs(last_d["c"] - w[0]["c"]) > 0.01:
                    n_dw += 1; hard_fails.append(f"{tk} D↔W 收盤不符(W{w[0]['c']} vs D{last_d['c']})")
        # H 全 RTH（非交易時段 bar 不該存在 → 硬失敗）
        if h:
            bad = [b["t"] for b in h[:60] if b["t"][11:16] not in mc.RTH_SLOTS]
            if bad:
                n_nonrth += 1; hard_fails.append(f"{tk} H 有非RTH bar {bad[:2]}")

    print(f"[verify] 掃 {len(tickers)} 支")
    print(f"  bar 少: {n_missing} | 分割未調乾淨: {n_cliff} | D↔W 不符: {n_dw} | H 非RTH: {n_nonrth} | 警告: {len(warnings)}")
    if warnings:
        print(f"\nℹ️ 警告 {len(warnings)} 筆（真實暴漲/分割/新上市，非失敗，前25）:")
        for p in warnings[:25]:
            print("   " + p)
    if hard_fails:
        print(f"\n❌ 硬失敗 {len(hard_fails)} 筆（真結構問題，前25）:")
        for p in hard_fails[:25]:
            print("   " + p)
        cli.close(); sys.exit(1)
    print("\n✅ 全過（無硬失敗）")
    cli.close(); sys.exit(0)


if __name__ == "__main__":
    main()
