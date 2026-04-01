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

    // Twelve Data endpoints
    const SPY_URL = `https://api.twelvedata.com/quote?symbol=SPY&apikey=${TWELVE_DATA_API_KEY}`;
    const VIX_URL = `https://api.twelvedata.com/quote?symbol=VIX&apikey=${TWELVE_DATA_API_KEY}`;

    async function fetchJSON(url) {
      const response = await fetch(url);

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`HTTP error ${response.status}: ${text}`);
      }

      return await response.json();
    }

    // Fetch data
    const [spyData, vixData] = await Promise.all([
      fetchJSON(SPY_URL),
      fetchJSON(VIX_URL)
    ]);

    // Safety checks
    if (!spyData || spyData.status === "error") {
      throw new Error(`SPY data error: ${spyData?.message || "unknown"}`);
    }

    const spyPrice = parseFloat(spyData.close);
    const spyChange = parseFloat(spyData.percent_change);
    const spyVolume = parseFloat(spyData.volume);

    const vixPrice = vixData?.close ? parseFloat(vixData.close) : "N/A";

    // Format
    const spyChangeFormatted =
      typeof spyChange === "number" ? spyChange.toFixed(2) : "N/A";

    const message = `
📊 Market Update

SPY:
Price: ${spyPrice}
Change: ${spyChangeFormatted}%
Volume: ${spyVolume}

VIX:
Price: ${vixPrice}

🧠 Twelve Data Feed
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

    if (!telegramResponse.ok) {
      const text = await telegramResponse.text();
      throw new Error(`Telegram error: ${text}`);
    }

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
