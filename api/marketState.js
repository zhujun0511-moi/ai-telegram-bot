export function analyzeMarketState({ price, volumeProfile }) {
  if (!price || !volumeProfile) {
    return {
      state: "UNKNOWN",
      bias: "NEUTRAL",
      position: "UNKNOWN"
    };
  }

  const { poc, hvn, lvn } = volumeProfile;

  const pocPrice = poc?.price;

  // -----------------------------
  // 1. 判断相对 POC 位置
  // -----------------------------
  let bias = "NEUTRAL";

  if (price > pocPrice) {
    bias = "BULLISH";
  } else if (price < pocPrice) {
    bias = "BEARISH";
  }

  // -----------------------------
  // 2. 判断是否在 HVN 区域
  // -----------------------------
  let inHVN = false;

  if (hvn && hvn.length > 0) {
    inHVN = hvn.some(level => {
      const distance = Math.abs(price - level.price);
      const tolerance = (volumeProfile.range.max - volumeProfile.range.min) * 0.01; // 1% 容忍区间
      return distance < tolerance;
    });
  }

  // -----------------------------
  // 3. 判断是否在 LVN 区域
  // -----------------------------
  let inLVN = false;

  if (lvn && lvn.length > 0) {
    inLVN = lvn.some(level => {
      const distance = Math.abs(price - level.price);
      const tolerance = (volumeProfile.range.max - volumeProfile.range.min) * 0.005; // 更窄
      return distance < tolerance;
    });
  }

  // -----------------------------
  // 4. 判断市场状态（Trend / Range）
  // -----------------------------
  let state = "RANGE";

  if (!inHVN && inLVN) {
    state = "TREND";
  }

  if (inHVN) {
    state = "RANGE";
  }

  // -----------------------------
  // 5. 判断位置描述
  // -----------------------------
  let position = "UNKNOWN";

  if (price > pocPrice) {
    position = inHVN ? "ABOVE_POC_IN_HVN" : "ABOVE_POC";
  } else if (price < pocPrice) {
    position = inHVN ? "BELOW_POC_IN_HVN" : "BELOW_POC";
  } else {
    position = "AT_POC";
  }

  return {
    state,
    bias,
    position,
    inHVN,
    inLVN,
    poc: pocPrice
  };
}
