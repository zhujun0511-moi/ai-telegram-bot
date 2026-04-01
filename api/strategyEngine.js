export function buildStrategy({ price, marketState, volumeProfile, signal }) {
  const { poc, hvn, lvn } = volumeProfile;
  const pocPrice = poc?.price;

  // -----------------------------
  // Helper: nearest levels
  // -----------------------------
  function nearestAbove(levels, price) {
    return levels
      .map(l => l.price)
      .filter(p => p > price)
      .sort((a, b) => a - b)[0] || null;
  }

  function nearestBelow(levels, price) {
    return levels
      .map(l => l.price)
      .filter(p => p < price)
      .sort((a, b) => b - a)[0] || null;
  }

  // -----------------------------
  // Key levels
  // -----------------------------
  const hvnAbove = nearestAbove(hvn, price);
  const hvnBelow = nearestBelow(hvn, price);

  const lvnAbove = nearestAbove(lvn, price);
  const lvnBelow = nearestBelow(lvn, price);

  // -----------------------------
  // Scenario Definitions
  // -----------------------------
  const scenarios = [];

  // =============================
  // Scenario 1: Trend Continuation
  // =============================
  if (marketState.state === "TREND") {
    scenarios.push({
      name: "TREND_CONTINUATION",
      condition: "Momentum persists",
      path: [
        price,
        hvnAbove || lvnAbove,
        hvnAbove ? hvnAbove + (hvnAbove - price) * 0.5 : null
      ],
      target: hvnAbove || lvnAbove,
      invalidation: pocPrice,
      bias: marketState.bias
    });
  }

  // =============================
  // Scenario 2: Mean Reversion
  // =============================
  if (marketState.state === "RANGE") {
    scenarios.push({
      name: "MEAN_REVERSION",
      condition: "Price oscillates around POC",
      path: [
        price,
        pocPrice,
        hvnBelow || hvnAbove
      ],
      target: pocPrice,
      invalidation: lvnAbove || lvnBelow,
      bias: "NEUTRAL"
    });
  }

  // =============================
  // Scenario 3: Breakout
  // =============================
  const isAbovePOC = price > pocPrice;
  const isBelowPOC = price < pocPrice;

  if (isAbovePOC) {
    scenarios.push({
      name: "BREAKOUT_UP",
      condition: "Hold above HVN cluster",
      path: [
        price,
        hvnAbove,
        lvnAbove
      ],
      target: lvnAbove,
      invalidation: pocPrice,
      bias: "BULLISH"
    });
  }

  if (isBelowPOC) {
    scenarios.push({
      name: "BREAKOUT_DOWN",
      condition: "Loss of HVN support",
      path: [
        price,
        hvnBelow,
        lvnBelow
      ],
      target: lvnBelow,
      invalidation: pocPrice,
      bias: "BEARISH"
    });
  }

  // =============================
  // Scenario 4: Liquidity Sweep
  // =============================
  scenarios.push({
    name: "LIQUIDITY_SWEEP",
    condition: "Price wicks into LVN then reverses",
    path: [
      price,
      lvnAbove,
      pocPrice
    ],
    target: pocPrice,
    invalidation: lvnAbove,
    bias: marketState.bias
  });

  // -----------------------------
  // Strategy Summary
  // -----------------------------
  const primaryScenario = scenarios.find(s => s.bias === signal.signal) || scenarios[0];

  return {
    primary: primaryScenario,
    scenarios,
    context: {
      poc: pocPrice,
      hvnAbove,
      hvnBelow,
      lvnAbove,
      lvnBelow
    }
  };
}
