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
          content: "总结过去24小时内全球最重要的宏观或金融事件（最多3条），每条必须说明对SPX或风险资产的影响。最后给出整体市场风险判断（上涨/下跌概率）。不要预测具体点位，不要编造数据。如果需要给出日内判断，只能给出方向性倾向（偏多/偏空/震荡），不能给具体价格。输出必须简洁、有结构。"
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
