export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  return `${value.toFixed(1)}%`;
}

export function formatEdge(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "n/a";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return "нет данных";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "нет данных";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)} сек`;
  }
  const minutes = Math.floor(seconds / 60);
  const restSeconds = Math.round(seconds % 60);
  if (restSeconds === 0) {
    return `${minutes} мин`;
  }
  return `${minutes} мин ${restSeconds} сек`;
}

export function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "$0.00";
  }
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}$${abs.toFixed(2)}`;
}

export function shortenQuestion(question: string, maxLength = 90): string {
  if (question.length <= maxLength) {
    return question;
  }
  return `${question.slice(0, maxLength - 3)}...`;
}
