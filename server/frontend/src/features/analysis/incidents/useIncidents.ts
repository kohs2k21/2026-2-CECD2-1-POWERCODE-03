import { useAccountQuery } from "@/lib/query";
import { readIncidentDetail, readIncidentList, readIncidentMembers } from "./incidentApi";

export const incidentListQueryName = "analysis-incidents";
export const incidentDetailQueryName = "analysis-incident";
export const incidentMembersQueryName = "analysis-incident-members";

/** 사건 목록(흐름). 기간·커서를 바꾸는 동안 직전 결과를 유지한다. 실시간 재조회 없음(Detector 묶음은 주기 작업 — 다시 시도로 재조회). */
export const useIncidentList = (from: string | null, cursor: string | null, all: boolean, enabled = true) =>
  useAccountQuery({
    name: incidentListQueryName,
    params: [from, cursor, all],
    read: (signal) => readIncidentList({ from, cursor, all }, signal),
    keepPrevious: true,
    enabled,
  });
export const useIncidentDetail = (id: string) =>
  useAccountQuery({ name: incidentDetailQueryName, params: [id], read: (signal) => readIncidentDetail(id, signal), enabled: Boolean(id) });
/** 구성원 한 쪽(거래별). 더 보기는 쪽마다 이 조회를 하나씩 더한다. */
export const useIncidentMembers = (id: string, cursor: string | null) =>
  useAccountQuery({
    name: incidentMembersQueryName,
    params: [id, cursor],
    read: (signal) => readIncidentMembers(id, cursor, signal),
    enabled: Boolean(id),
  });
