export function buildVolumeProfile(candles, bins = 30) {
  if (!candles || candles.length === 0) {
    return null;
  }

  let minPrice = Infinity;
  let maxPrice = -Infinity;

  candles.forEach(c => {
    if (c.low < minPrice) minPrice = c.low;
    if (c.high > maxPrice) maxPrice = c.high;
  });

  const range = maxPrice - minPrice;
  const binSize = range / bins;

  const volumeBins = new Array(bins).fill(0);

  candles.forEach(c => {
    const typicalPrice = (c.high + c.low + c.close) / 3;

    const index = Math.floor((typicalPrice - minPrice) / binSize);
    const safeIndex = Math.max(0, Math.min(bins - 1, index));

    volumeBins[safeIndex] += c.volume;
  });

  // POC
  let pocIndex = 0;
  let maxVolume = 0;

  volumeBins.forEach((vol, i) => {
    if (vol > maxVolume) {
      maxVolume = vol;
      pocIndex = i;
    }
  });

  const pocPrice = minPrice + pocIndex * binSize;

  const avgVolume =
    volumeBins.reduce((a, b) => a + b, 0) / bins;

  const hvn = [];
  const lvn = [];

  volumeBins.forEach((vol, i) => {
    const priceLevel = minPrice + i * binSize;

    if (vol > avgVolume * 1.2) {
      hvn.push({
        price: priceLevel,
        volume: vol
      });
    }

    if (vol < avgVolume * 0.8) {
      lvn.push({
        price: priceLevel,
        volume: vol
      });
    }
  });

  return {
    poc: {
      price: pocPrice,
      volume: maxVolume
    },
    hvn,
    lvn,
    bins: volumeBins,
    range: {
      min: minPrice,
      max: maxPrice
    }
  };
}
