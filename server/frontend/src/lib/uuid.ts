/**
 * RFC 4122 v4 UUID 문자열. `crypto.randomUUID`는 보안 컨텍스트(HTTPS·localhost)에서만 있으므로,
 * HTTP 접속(비보안 컨텍스트)에서는 어디서나 되는 `crypto.getRandomValues`로 같은 형식을 만든다.
 */
export const randomUuid = (): string => {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // 버전 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // 변형 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};
