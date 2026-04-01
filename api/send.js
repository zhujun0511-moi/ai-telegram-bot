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
基于提供的新闻，进行交易级分析（不是新闻总结）。

必须严格按结构输出：

1）关键事件（最多3条）
- 每条：事件 + 对SPX直接影响（一句话）

2）SPX初始判断（MANDATORY）
- 预期开盘：高开 / 低开 / 平开
- 市场情绪：risk-on / risk-off
- 强度判断：强 / 中 / 弱

3）板块结构（MANDATORY）
- 哪些板块可能明显强于市场
- 哪些明显弱于市场
- 必须说明因果链（例如：油价→能源）

4）敏感个股（MANDATORY）
- 不是随机列举
- 必须对应板块
- 标注：可能高开 / 相对强 / 仅受影响

5）关键判断（MANDATORY）
- 当前事件是：
  → 新信息（未计价）
  → 还是已被市场计价（price-in）

6）最终结论
- SPX上涨/下跌概率（%）
- 日内方向：偏多 / 偏空 / 震荡

禁止泛泛而谈
禁止重复新闻
必须偏向交易分析
`;

  // ✅ Step 3：拼接（关键一步）
  const fullPrompt = `
Here are today's key headlines:

${newsInput}

You MUST base your analysis on the above information.
Do NOT ignore them.

If information is insufficient, you must say "信息不足".

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
  const text = aiData.choices?.[0]?.message?.content || "AI未返回有效内容";

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
