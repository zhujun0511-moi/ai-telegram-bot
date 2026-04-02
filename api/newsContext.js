export async function buildNewsContext({ headlines = [] }) {
  // headlines: array of strings or objects
  // e.g. ["Fed signals rate pause", ...]

  if (!headlines || headlines.length === 0) {
    return {
      regime: "NORMAL",
      impact: "LOW",
      direction: "NEUTRAL",
      confidence: 0,
      raw: []
    };
  }

  // -----------------------------
  // Simple keyword-based heuristic (fast + stable)
  // -----------------------------
  const macroKeywords = ["fed", "rate", "cpi", "inflation", "jobs", "nfp", "ppi"];
  const riskOnKeywords = ["growth", "upgrade", "beat", "optimistic"];
  const riskOffKeywords = ["recession", "cut", "weak", "miss", "downgrade"];

  let score = 0;
  let directionScore = 0;

  const normalized = headlines.map(h => h.toLowerCase());

  for (const text of normalized) {
    // Macro detection
    if (macroKeywords.some(k => text.includes(k))) {
      score += 2;
    }

    // Risk-on
    if (riskOnKeywords.some(k => text.includes(k))) {
      directionScore += 1;
    }

    // Risk-off
    if (riskOffKeywords.some(k => text.includes(k))) {
      directionScore -= 1;
    }
  }

  // -----------------------------
  // Regime classification
  // -----------------------------
  let regime = "NORMAL";
  let impact = "LOW";

  if (score >= 4) {
    regime = "EVENT";
    impact = "MEDIUM";
  }

  if (score >= 6) {
    regime = "SHOCK";
    impact = "HIGH";
  }

  // -----------------------------
  // Direction inference (weak signal)
  // -----------------------------
  let direction = "NEUTRAL";

  if (directionScore > 0) direction = "RISK_ON";
  if (directionScore < 0) direction = "RISK_OFF";

  // -----------------------------
  // Confidence (heuristic)
  // -----------------------------
  let confidence = Math.min(100, score * 10);

  // -----------------------------
  // Output
  // -----------------------------
  return {
    regime,
    impact,
    direction,
    confidence,
    score,
    directionScore,
    raw: headlines
  };
}
