# -*- coding: utf-8 -*-
"""
backup_bars.py — 覆蓋前備份 Bars_D / Bars_H（§14.4 step 0，不可逆操作的保險）

把現有全部 Bars_{ticker} 的 D / H period 文件 dump 成本地 gzip JSONL（一 period 一檔），
每行 = {ticker, period, bars, updated_at}。跑遷移前**必先**跑這支。

用法（本地）：
  MIG_ENV_FILE=C:/Users/zhuju/pass.env.txt python backup_bars.py
  （或先設好 MONGO_URI 環境變數）
輸出：./backup_YYYYMMDD_HHMM/bars_D.jsonl.gz、bars_H.jsonl.gz、manifest.json
"""
import os, sys, io, gzip, json, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import mig_common as mc


def main(periods=("D", "H")):
    cli, sd = mc.get_stock_db()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"backup_{stamp}")
    os.makedirs(outdir, exist_ok=True)
    print(f"[backup] 輸出目錄: {outdir}")

    names = [c for c in sd.list_collection_names() if c.startswith("Bars_")]
    print(f"[backup] 掃到 {len(names)} 個 Bars_* collection")

    manifest = {"created_at": stamp, "periods": {}, "source": "StockData"}
    for period in periods:
        path = os.path.join(outdir, f"bars_{period}.jsonl.gz")
        n_docs = 0
        n_bars = 0
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for cn in names:
                doc = sd[cn].find_one({"period": period})
                if not doc:
                    continue
                doc.pop("_id", None)
                # updated_at datetime → ISO 字串（JSON 安全）
                ua = doc.get("updated_at")
                if isinstance(ua, datetime.datetime):
                    doc["updated_at"] = ua.isoformat()
                f.write(json.dumps(doc, ensure_ascii=False, default=str) + "\n")
                n_docs += 1
                n_bars += len(doc.get("bars", []))
        size_mb = os.path.getsize(path) / 1e6
        manifest["periods"][period] = {"docs": n_docs, "bars": n_bars, "file": os.path.basename(path),
                                       "size_mb": round(size_mb, 2)}
        print(f"[backup] {period}: {n_docs} 檔 / {n_bars} bars → {os.path.basename(path)} ({size_mb:.1f}MB)")

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"[backup] ✅ 完成。manifest: {os.path.join(outdir, 'manifest.json')}")
    print("[backup] 還原方式：讀 jsonl.gz 每行 → col.replace_one({ticker,period},doc,upsert=True)（見 restore 註解）")
    cli.close()
    return outdir


# 還原（緊急用；預設不執行，需明確傳 --restore <dir>）：
def restore(dirpath, periods=("D", "H")):
    cli, sd = mc.get_stock_db()
    import pymongo
    for period in periods:
        path = os.path.join(dirpath, f"bars_{period}.jsonl.gz")
        if not os.path.isfile(path):
            print(f"[restore] 跳過（無檔）: {path}"); continue
        n = 0
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                doc = json.loads(line)
                tk = doc["ticker"]
                sd[f"Bars_{tk}"].replace_one({"ticker": tk, "period": period}, doc, upsert=True)
                n += 1
        print(f"[restore] {period}: 還原 {n} 檔")
    cli.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--restore":
        restore(sys.argv[2])
    else:
        main()
