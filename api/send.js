export default async function handler(req, res) {
  try {
    // =========================
    // 1️⃣ 从环境变量读取密钥（安全）
    // =========================
    const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY;
    const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
    const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;

    // =========================
    // 2️⃣ 获取市场数据（SPY + VIX）
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

    const spyPrice = market.spy?.regularMarketPrice;
    const spyChange = market.spy?.regularMarketChangePercent;

    const vixPrice = market.vix?.regularMarketPrice;
    const vixChange = market.vix?.regularMarketChangePercent;

    // =========================
    // 3️⃣ 构造 prompt
    // =========================
    const prompt = `
当前市场数据：
SPY: ${spyPrice} (${spyChange}%)
VIX: ${vixPrice} (${vixChange}%)

任务：
总结过去24小时内全球宏观或金融相关信息（如无外部新闻数据，请基于市场数据进行结构化推理），并完成：

1）判断当前市场属于 risk-on 还是 risk-off  
2）判断是否存在预期差  
3）判断当前市场状态（新信息 / 已计价 / 持续演变）  
4）给出最终结论：
- 方向（偏多 / 偏空 / 不确定）
- 概率（0-100%）
- 一句话核心逻辑

要求：
- 不要编造具体新闻事实
- 不要使用具体价格预测
- 不确定时必须明确说明
`;

    // =========================
    // 4️⃣ 调用 OpenRouter
    // =========================
    const aiRes = await fetch("https://openrouter.ai/api/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${OPENROUTER_API_KEY}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "openai/gpt-4o-mini",
        messages: [
          { role: "user", content: prompt }
        ]
      })
    });

    const aiData = await aiRes.json();
    const aiText = aiData?.choices?.[0]?.message?.content || "AI 无返回内容";

    // =========================
    // 5️⃣ 发送到 Telegram
    // =========================
    await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        chat_id: TELEGRAM_CHAT_ID,
        text: aiText
      })
    });

    res.status(200).json({ success: true });

  } catch (error) {
    console.error(error);
    res.status(500).json({ error: error.toString() });
  }
}
