export default async function handler(req, res) {
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
          content: "总结过去24小时内全球最重要的宏观或金融事件。每条必须包含：1）事件本身（一句话）2）对SPX或风险资产的直接影响（只讲结果）。最后给出整体市场判断：上涨/下跌概率。日内只允许给出方向性倾向（偏多/偏空/震荡）。禁止预测具体点位，禁止编造数据，禁止冗余解释。输出必须结构清晰、优先给结论。"
        }
      ]
    })
  });

  const aiData = await aiRes.json();
  const text = aiData.choices[0].message.content;

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
