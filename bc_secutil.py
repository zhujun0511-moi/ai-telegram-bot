# -*- coding: utf-8 -*-
"""
bc_secutil.py — 密鑰遮蔽（2026-08-06 全域密鑰遮蔽，見 DANGER_ZONES 2026-08-05 §5）

洩漏機制：對含 apiKey= 的 Polygon URL 發請求，連線例外的 str(e) 會帶出整條含密鑰的 URL；
_log/print 這條 {e} 就把密鑰寫進 GHA log。scrub() 是全域出口遮蔽：凡會進 log 的例外訊息、URL 先過它。
（各中心獨立部署，本檔為 DC config.scrub / AC / comm-hub / BC.p 同一份實作的 BC 副本。）
"""
import os
import re

_SECRET_ENV_NAMES = (
    "POLYGON_KEY", "TWELVE_KEY", "TWELVE_KEY_2", "MONGO_URI",
    "GH_TOKEN", "HF_TOKEN", "WEBHOOK_SECRET", "MP_API_KEY",
)
_QS_SECRET_RE = re.compile(
    r'(?i)(apikey|api_key|secret|token|password|pwd|access_token|sig|signature)=([^&\s"\'#]+)'
)
_MONGO_CRED_RE = re.compile(r'(mongodb(?:\+srv)?://)[^:/@\s]+:[^@\s]+@')


def scrub(s):
    """遮蔽字串中的密鑰值，避免密鑰外洩到 GHA log。
    遮蔽：① query-param 密鑰（apikey/secret/token…=值）② mongodb URI 帳密 ③ 已知 env 密鑰值。
    None/非字串安全處理。"""
    if s is None:
        return s
    try:
        s = str(s)
    except Exception:
        return "***"
    # env 精確值先替換（含特殊字元/密碼含 @ 也整段遮；必須在正則前，否則正則改寫後精確比對失配）
    for _name in _SECRET_ENV_NAMES:
        _val = os.getenv(_name, "")
        if _val and len(_val) >= 8:
            s = s.replace(_val, "***")
    s = _QS_SECRET_RE.sub(r'\1=***', s)
    s = _MONGO_CRED_RE.sub(r'\1***:***@', s)
    return s
