export function initials(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function avatarStyle(authorColor?: string) {
  return authorColor ? { backgroundColor: authorColor } : undefined;
}

export function productivityTone(productivity: number) {
  const value = Number.isFinite(productivity) ? productivity : 0;

  if (value > 100) {
    return "overdrive";
  }

  if (value > 80) {
    return "good";
  }

  if (value >= 50) {
    return "warning";
  }

  return "bad";
}

export function productivityClassName(productivity: number) {
  return `metric-value ${productivityTone(productivity)}`;
}

export function breakTone(seconds: number) {
  if (seconds <= 0) {
    return "neutral";
  }

  return seconds > 61 * 60 ? "bad" : "good";
}

export function breakClassName(seconds: number) {
  return `metric-value ${breakTone(seconds)}`;
}
