export default async function handler(req, res) {
  try {
    // =========================
    // 1️⃣ 获取市场数据（SPY + VIX）
    // =========================
    async function getMarketData() {
      const spyRes = await fetch("https://query1.finance.yahoo.com/v7/finance/quote?symbols=SPY");
      const spyJson = await spyRes.json();

      const vixRes = await fetch("https://query1.finance.yahoo.com/v7/finance/quote?symbols=^VIX");
      const vixJson = await vixRes.json();

      return {
        spy: spyJson.quoteResponse.result[0],
        vix: vixJson.quoteResponse.result[0]
      };
    }

    const market = await getMarketData();

    const spyPrice = market.spy.regularMarketPrice;
    const spyChange = market.spy.regularMarketChangePercent;

    const vixPrice = market.vix.regularMarketPrice;
    const vixChange = market.vix.regularMarketChangePercent;

    // =========================
    // 2️⃣ 你的AI分析内容
    // =========================
    const content = `
当前市场数据：
SPY: ${spyPrice} (${spyChange}%)
VIX: ${vixPrice} (${vixChange}%)

请分析：
1）这些数据代表risk-on还是risk-off？
2）市场是否已经消化利空？
3）结合宏观事件（最多2条），判断当前市场状态（新信息 / 持续 / 已计价）
4）给出最终判断：
- 方向（偏多 / 偏空 / 不确定）
- 概率（%）
- 一句话核心逻辑

禁止编造数据，必须基于上述市场数据推理。
`;

    // =========================
    // 3️⃣ 调用 OpenRouter
    // =========================
    const aiRes = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": "Bearer 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",
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "openai/gpt-4o-mini",
        messages: [
          { role: "user", content }
        ]
      })
    });

    const aiData = await aiRes.json();
    const aiText = aiData.choices[0].message.content;

    // =========================
    // 4️⃣ 发送到 Telegram
    // =========================
    await fetch(`https://api.telegram.org/bot8514143844:AAGyhAYDadb6l2MB5CfjmWsWHlg2_O7xELU/sendMessage`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        chat_id: "6708719654",
        text: aiText
      })
    });

    res.status(200).json({ success: true });

  } catch (error) {
    res.status(500).json({ error: error.toString() });
  }
}
