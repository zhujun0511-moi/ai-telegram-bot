export default async function handler(req, res) {

  // ✅ Step 1：加入新闻输入（你刚刚设计的核心）
  const newsInput = `
[Geopolitics]
- US-Iran tensions escalating, potential military conflict

[Macro]
- Oil prices moving higher overnight

[Market]
- US equity futures slightly down, risk-off tone

[Sector Signals]
- Energy sector (XLE) likely to benefit from rising oil
- Broad market may face pressure due to geopolitical risk

[Stock Signals]
- XOM, CVX (oil majors)
`;

  // ✅ Step 2：你的原始 prompt（保留）
  const basePrompt = `
总结过去24小时内全球最重要的宏观或金融事件。每条必须包含：
1）事件本身（一句话）
2）对SPX或风险资产的直接影响（只讲结果）

最后给出整体市场判断：
- 上涨/下跌概率
- 日内方向（偏多/偏空/震荡）

禁止预测具体点位
禁止编造数据
禁止冗余解释
输出必须结构清晰、优先给结论
`;

  // ✅ Step 3：拼接（关键一步）
  const fullPrompt = `
Here are today's key headlines:

${newsInput}

You MUST base your analysis on the above information.
Do NOT ignore them.

${basePrompt}

Final answer must be in Chinese.
`;

  // ✅ Step 4：发送给 OpenRouter
  const aiRes = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: "openai/gpt-4o-mini",
      messages: [
        {
          role: "user",
          content: fullPrompt
        }
      ]
    })
  });

  const aiData = await aiRes.json();
  const text = aiData.choices[0].message.content;

  // ✅ Step 5：发送到 Telegram
  await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      chat_id: process.env.CHAT_ID,
      text: text
    })
  });

  res.status(200).json({ success: true });
}
