import { useAccountQuery } from "@/lib/query";
import { readProtocolDrafts } from "../ruleSetApi";

export const protocolDraftsQueryName = "protocol-drafts";

/** 관측 경로 초안(관리자). interfaceId null = 인터페이스 요약 목록, 값 = 그 인터페이스 경로까지. enabled false면 읽지 않는다(상세는 단추를 눌렀을 때만). */
export const useProtocolDrafts = (interfaceId: string | null, enabled = true) =>
  useAccountQuery({
    name: protocolDraftsQueryName, params: [interfaceId], adminOnly: true, enabled,
    read: (signal) => readProtocolDrafts(interfaceId, signal),
  });
