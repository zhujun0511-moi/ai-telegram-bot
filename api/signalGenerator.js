export function generateSignals({ price, marketState, volumeProfile }) {
  if (!marketState || !volumeProfile) {
    throw new Error("Missing inputs for signal generation");
  }

  const { poc, hvn, lvn } = volumeProfile;

  const pocPrice = poc?.price;

  // -----------------------------
  // Helper: find nearest levels
  // -----------------------------
  function findNearestLevels(price, levels) {
    const sorted = [...levels].sort((a, b) => a.price - b.price);

    let below = null;
    let above = null;

    for (let i = 0; i < sorted.length; i++) {
      if (sorted[i].price < price) {
        below = sorted[i];
      }
      if (sorted[i].price > price && !above) {
        above = sorted[i];
        break;
      }
    }

    return { below, above };
  }

  const hvnLevels = findNearestLevels(price, hvn);
  const lvnLevels = findNearestLevels(price, lvn);

  // -----------------------------
  // Structure logic
  // -----------------------------
  let bias = "NEUTRAL";
  let targets = [];
  let supports = [];
  let resistances = [];

  // Bias from market state
  if (marketState.bias === "BULLISH") bias = "LONG";
  if (marketState.bias === "BEARISH") bias = "SHORT";

  // Position relative to POC
  const abovePOC = price > pocPrice;
  const belowPOC = price < pocPrice;

  // -----------------------------
  // Target logic
  // -----------------------------

  // Bullish scenario
  if (bias === "LONG") {
    if (abovePOC) {
      // Target next HVN / upper LVN
      if (hvnLevels.above) targets.push(hvnLevels.above.price);
      if (lvnLevels.above) targets.push(lvnLevels.above.price);
    } else {
      // mean reversion to POC
      targets.push(pocPrice);
    }
  }

  // Bearish scenario
  if (bias === "SHORT") {
    if (belowPOC) {
      if (hvnLevels.below) targets.push(hvnLevels.below.price);
      if (lvnLevels.below) targets.push(lvnLevels.below.price);
    } else {
      targets.push(pocPrice);
    }
  }

  // -----------------------------
  // Support / Resistance
  // -----------------------------
  hvn.forEach(level => {
    if (level.price < price) supports.push(level.price);
    if (level.price > price) resistances.push(level.price);
  });

  supports = supports.sort((a, b) => b - a).slice(0, 3);
  resistances = resistances.sort((a, b) => a - b).slice(0, 3);

  // -----------------------------
  // Signal decision
  // -----------------------------
  let signal = "NEUTRAL";

  if (bias === "LONG" && abovePOC) signal = "LONG";
  if (bias === "SHORT" && belowPOC) signal = "SHORT";

  // -----------------------------
  // Confidence heuristic
  // -----------------------------
  let confidence = 50;

  if (marketState.state === "TREND") confidence += 20;
  if (marketState.inHVN) confidence += 10;
  if (marketState.inLVN) confidence -= 5;

  confidence = Math.max(0, Math.min(100, confidence));

  return {
    signal,
    bias,
    confidence,
    targets: [...new Set(targets)],
    supports,
    resistances,
    poc: pocPrice
  };
}
