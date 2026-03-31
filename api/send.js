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
          content: "给我一段今日市场简报（简短）"
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
