/** 운영 현황 표시 형식. 값이 없으면 호출하는 쪽에서 "—" 또는 "측정 불가"를 쓴다(추정값 금지). */
export const DASH = "—";
export const UNMEASURED = "측정 불가";

const two = (n: number) => String(n).padStart(2, "0");
/** 시:분:초(와이어프레임 clock). */
export const clockText = (iso: string | null | undefined) => {
  if (!iso) return DASH;
  const d = new Date(iso);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
};
/** 월/일 시:분(:초)(와이어프레임 stamp). */
export const stampText = (iso: string | null | undefined, seconds = false) => {
  if (!iso) return DASH;
  const d = new Date(iso);
  return `${two(d.getMonth() + 1)}/${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}${seconds ? `:${two(d.getSeconds())}` : ""}`;
};
/** YYYY-MM-DD → MM/DD. */
export const dayText = (day: string) => `${day.slice(5, 7)}/${day.slice(8, 10)}`;
export const hourLabel = (iso: string) => `${two(new Date(iso).getHours())}시`;
export const numberText = (n: number) => n.toLocaleString("ko-KR");
export const percentText = (ratio: number) => `${(ratio * 100).toFixed(ratio > 0 && ratio < 0.001 ? 3 : 2).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")}%`;
export const msText = (value: number) => (value >= 1000 ? `${numberText(Math.round(value))} ms` : `${Math.round(value)} ms`);
/** 대기 시간: 1초 미만 ms, 1분 미만 초(소수 1자리), 1시간 미만 분·초, 그 이상 시간·분(내림). */
export const durationText = (value: number) => {
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60_000) return `${(Math.floor(value / 100) / 10).toFixed(1)}초`;
  const seconds = Math.floor(value / 1000);
  if (seconds < 3600) {
    const rest = seconds % 60;
    return `${Math.floor(seconds / 60)}분${rest ? ` ${rest}초` : ""}`;
  }
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${numberText(Math.floor(seconds / 3600))}시간${minutes ? ` ${minutes}분` : ""}`;
};
export const bytesText = (bytes: number) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
};
