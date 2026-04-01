import { analyzeMarketState } from './marketState.js';

export default async function handler(req, res) {
  try {
    const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
    const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
    const TWELVE_DATA_API_KEY = process.env.TWELVE_DATA_API_KEY;

    if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID || !TWELVE_DATA_API_KEY) {
      return res.status(500).json({
        error: "Missing environment variables"
      });
    }

    // -----------------------------
    // Endpoints
    // -----------------------------
    const SPY_QUOTE_URL = `https://api.twelvedata.com/quote?symbol=SPY&apikey=${TWELVE_DATA_API_KEY}`;
    const SPY_TS_URL = `https://api.twelvedata.com/time_series?symbol=SPY&interval=1h&outputsize=50&apikey=${TWELVE_DATA_API_KEY}`;
    const VIX_URL = `https://api.twelvedata.com/quote?symbol=VIX&apikey=${TWELVE_DATA_API_KEY}`;

    async function fetchJSON(url) {
      const response = await fetch(url);

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`HTTP error ${response.status}: ${text}`);
      }

      return await response.json();
    }

    // -----------------------------
    // Fetch data
    // -----------------------------
    const [spyQuote, spyCandlesData, vixData] = await Promise.all([
      fetchJSON(SPY_QUOTE_URL),
      fetchJSON(SPY_TS_URL),
      fetchJSON(VIX_URL)
    ]);

    if (!spyQuote || spyQuote.status === "error") {
      throw new Error(`SPY quote error: ${spyQuote?.message || "unknown"}`);
    }

    if (!spyCandlesData || spyCandlesData.status === "error") {
      throw new Error(`SPY time series error: ${spyCandlesData?.message || "unknown"}`);
    }

    // -----------------------------
    // Parse quote
    // -----------------------------
    const spyPrice = parseFloat(spyQuote.close);
    const spyChange = parseFloat(spyQuote.percent_change);
    const spyVolume = parseFloat(spyQuote.volume);

    // -----------------------------
    // Parse candles
    // -----------------------------
    const candles = spyCandlesData.values.map(item => ({
      open: parseFloat(item.open),
      high: parseFloat(item.high),
      low: parseFloat(item.low),
      close: parseFloat(item.close),
      volume: parseFloat(item.volume)
    }));

    // -----------------------------
    // Volume Profile
    // -----------------------------
    const { buildVolumeProfile } = await import('./volumeProfile.js');

    const vp = buildVolumeProfile(candles, 30);

    const pocPrice = vp?.poc?.price;
    const hvnCount = vp?.hvn?.length || 0;
    const lvnCount = vp?.lvn?.length || 0;

    // -----------------------------
    // Market State
    // -----------------------------
    const marketState = analyzeMarketState({
      price: spyPrice,
      volumeProfile: vp
    });

    // -----------------------------
    // VIX
    // -----------------------------
    const vixPrice = vixData?.close ? parseFloat(vixData.close) : "N/A";

    const spyChangeFormatted =
      typeof spyChange === "number" ? spyChange.toFixed(2) : "N/A";

    // -----------------------------
    // Message
    // -----------------------------
    const message = `
📊 Market Update

SPY:
Price: ${spyPrice}
Change: ${spyChangeFormatted}%
Volume: ${spyVolume}

Volume Profile:
POC: ${pocPrice}
HVN Levels: ${hvnCount}
LVN Levels: ${lvnCount}

Market State:
State: ${marketState.state}
Bias: ${marketState.bias}
Position: ${marketState.position}
In HVN: ${marketState.inHVN}
In LVN: ${marketState.inLVN}

VIX:
Price: ${vixPrice}

🧠 Twelve Data Feed
`;

    // -----------------------------
    // Send Telegram
    // -----------------------------
    const telegramUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;

    const telegramResponse = await fetch(telegramUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        chat_id: TELEGRAM_CHAT_ID,
        text: message
      })
    });

    if (!telegramResponse.ok) {
      const text = await telegramResponse.text();
      throw new Error(`Telegram error: ${text}`);
    }

    const telegramResult = await telegramResponse.json();

    return res.status(200).json({
      success: true,
      telegram: telegramResult,
      marketState,
      volumeProfile: vp
    });

  } catch (error) {
    console.error("Error:", error);

    return res.status(500).json({
      error: error.message
    });
  }
}
