export default async function handler(req, res) {
  try {
    const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
    const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;

    if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
      return res.status(500).json({
        error: "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
      });
    }

    // Yahoo Finance endpoints
    const SPY_URL =
      "https://query1.finance.yahoo.com/v7/finance/quote?symbols=SPY";
    const VIX_URL =
      "https://query1.finance.yahoo.com/v7/finance/quote?symbols=%5EVIX";

    // Fetch function with safety
    async function fetchJSON(url) {
  const response = await fetch(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept": "application/json"
    }
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`HTTP error ${response.status}: ${text}`);
  }

  return await response.json();
}

    // Fetch market data
    const [spyData, vixData] = await Promise.all([
      fetchJSON(SPY_URL),
      fetchJSON(VIX_URL)
    ]);

    // Safe extraction
    const spy = spyData?.quoteResponse?.result?.[0];
    const vix = vixData?.quoteResponse?.result?.[0];

    if (!spy) {
      throw new Error("SPY data unavailable");
    }

    // Extract fields
    const spyPrice = spy.regularMarketPrice;
    const spyChange = spy.regularMarketChangePercent;
    const spyVolume = spy.regularMarketVolume;

    const vixPrice = vix?.regularMarketPrice ?? "N/A";

    // Format message
    const message = `
📊 Market Update

SPY:
Price: ${spyPrice}
Change: ${spyChange?.toFixed(2)}%
Volume: ${spyVolume}

VIX:
Price: ${vixPrice}

🧠 Sent from bot
`;

    // Send to Telegram
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

    const telegramResult = await telegramResponse.json();

    return res.status(200).json({
      success: true,
      telegram: telegramResult
    });

  } catch (error) {
    console.error("Error:", error);

    return res.status(500).json({
      error: error.message
    });
  }
}
