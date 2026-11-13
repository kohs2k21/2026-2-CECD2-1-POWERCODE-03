import { useAccountQuery } from "@/lib/query";
import { readResponseCode, readResponseCodes } from "./responseCodesApi";

/** 응답 코드 목록(검색어·유형별)·상세. 관리자 화면(탐지 설정)만 읽는다. 저장 뒤 다시 읽도록 staleTime 0. */
export const responseCodesQueryName = "response-codes";
export const useResponseCodes = (filter: { q: string; type: string }) =>
  useAccountQuery({
    name: responseCodesQueryName, params: [filter.q.trim(), filter.type], adminOnly: true, staleTime: 0, keepPrevious: true,
    read: (signal) => readResponseCodes(filter, signal),
  });
export const useResponseCode = (code: string) =>
  useAccountQuery({ name: "response-code", params: [code], adminOnly: true, staleTime: 0, read: (signal) => readResponseCode(code, signal) });
