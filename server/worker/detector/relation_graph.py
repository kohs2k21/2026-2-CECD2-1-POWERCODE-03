"""거래 내부 실행 그래프 층 A(`graph.layer = "instance"`)의 Python 구현 — contracts/process-graph.md.

- 백엔드 `server/backend/src/services/processGraph.ts`(`buildInstanceGraph`·`pickTransactionRow`·`graphValueAbsent`)와
  같은 입력에 같은 노드·간선을 낸다(공용 벡터 `process-graph-id-vectors.json`, Detector 시험 고정 데이터로 확인).
- DB를 읽지 않는 순수 함수다. 입력 행은 TS와 같은 camelCase 키·문자열 값(숫자 열은 SQL `::text`)이다.
- Detector는 이 그래프의 `precedes` 검증과 `hands_off` 결과만 쓴다(relation-rules.md 3.2). `contains` 검증 상태에는
  의존하지 않는다.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

# process-graph.md "없음 판정": ASCII 공백·탭·CR·LF만 앞뒤에서 지운다(Python strip()·JS trim()과 다르다).
_GRAPH_TRIM_CHARS = " \t\r\n"
DEPEND_PROCESS_ID = "DEPEND_PROCESS_ID"
# 0채움 2자리 끝자리 거래 ID: MESSAGE_ID 인계 기준선 산정 제외 구간(process-graph.md hands_off, 임시 기준).
# TS `/-0\d$/`와 같게: JS `\d`는 ASCII 숫자만, `$`(m 플래그 없음)는 문자열 끝만. Python `\d`는 유니코드 숫자,
# `$`는 끝 줄바꿈 앞에서도 맞으므로 `[0-9]`·`\Z`로 쓴다(예: "TX-0٣"·"TX-01\n"은 제외 구간이 아니다).
EXCLUDED_TRANSACTION_SUFFIX = re.compile(r"-0[0-9]\Z")


def graph_trim(value: str) -> str:
    return value.strip(_GRAPH_TRIM_CHARS)


def graph_value_absent(value: Any, field: str | None = None) -> bool:
    """계약 함수 `graph_value_absent`. Detector `source_absent`(method_results.py)와 바꿔 쓰지 않는다.

    없음: None, 또는 ASCII 공백·탭·CR·LF만 앞뒤에서 뺀 값이 "" 또는 "\\N". field가 DEPEND_PROCESS_ID일 때만
    뺀 값이 정확히 "NONE"(대소문자 구분)이어도 없음.
    """
    if value is None:
        return True
    trimmed = graph_trim(value)
    return trimmed in ("", "\\N") or (field == DEPEND_PROCESS_ID and trimmed == "NONE")


def _present(value: Any, field: str | None = None) -> bool:
    return not graph_value_absent(value, field)


def escape_id_component(value: str) -> str:
    """id 구성 요소의 유일한 변환: '%' → '%25' 다음 ':' → '%3A'."""
    return value.replace("%", "%25").replace(":", "%3A")


_esc = escape_id_component


def transaction_id(transaction_id_value: str) -> str:
    return f"transaction:{_esc(transaction_id_value)}"


def process_id(transaction_id_value: str, process_id_value: str) -> str:
    return f"process:{_esc(transaction_id_value)}:{_esc(process_id_value)}"


def message_id(execution_id: str) -> str:
    return f"message:{_esc(execution_id)}"


def unkeyed_id(observation_id: str) -> str:
    return f"unkeyed:{_esc(observation_id)}"


def missing_id(referencing_node_id: str, process_id_value: str) -> str:
    return f"missing:{referencing_node_id}:{_esc(process_id_value)}"


def edge_id(kind: str, from_id: str, to_id: str) -> str:
    return f"{kind}:{from_id}:{to_id}"


def hands_off_id(from_id: str, to_id: str, message_node_id: str) -> str:
    return f"hands_off:{from_id}:{to_id}:{message_node_id}"


def retry_number(value: Any) -> float | None:
    """TS `retryNumber`(`Number(value)`, 유한값만)에 대응한다.

    전제: 입력은 numeric 열의 SQL `::text`(ASCII 10진 숫자·부호·소수점만)다. 그 밖의 문자열에서는 Python `float`와
    JS `Number`가 다르다 — 예: "0x10"(JS 16, Python 없음), "1_000"·"٣"·U+FEFF가 붙은 값(Python 숫자, JS NaN).
    원천 문자열을 직접 넣는 호출자를 만들면 엄격한 10진 해석으로 바꾼다(시험 test_relation_graph가 차이를 남겨 둔다).
    numeric 열의 `::text`도 `NaN`·`Infinity`·`-Infinity`는 낼 수 있다(PostgreSQL numeric 특수값). 이 값은 두 쪽 모두
    유한값이 아니라 없음이다 — Python `float`는 nan·inf가 되어 None, JS `Number`도 NaN·Infinity가 되어 null.
    """
    if not _present(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def retry_group_ambiguous(attempts: list[Mapping[str, Any]]) -> bool:
    """재시도 묶음 모호(process-graph.md "모호"): 실행마다 최신 리비전으로 줄인 뒤에도 같은 재시도 번호(또는 번호 없음)가
    2건 이상. 그래프 노드 `verification`과 프로토콜 경로(protocol_rules)가 같은 함수를 쓴다."""
    retries = [retry_number(attempt.get("retryCount")) for attempt in attempts]
    return len(attempts) > 1 and (None in retries or len(set(retries)) != len(retries))


def _by_retry(row: Mapping[str, Any]) -> tuple[float, str]:
    number = retry_number(row.get("retryCount"))
    return (math.inf if number is None else number, row["observationId"])


def pick_transaction_row(executions: int, missing_execution_id: int, missing_retry_count: int,
                         top: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, bool]:
    """거래 대표 TRANSACTION 행(process-graph.md "거래 노드 대표 행", relation-rules.md 7.2). (행, 모호 여부).

    입력: 같은 원본·같은 거래 ID의 TRANSACTION 실행(실행마다 최신 리비전) 수와 결측 수, 재시도 번호 큰 순 앞 2건
    (번호 없음이 맨 앞). 실행 0건 → (None, False), 1건 → 그 행. 2건 이상: 실행 id 없음·재시도 번호 없음이 하나라도
    있거나 가장 큰 재시도 번호가 2건 이상이면 모호, 아니면 재시도 번호가 가장 큰 실행.
    """
    if executions <= 0 or not top:
        return None, False
    if executions == 1:
        return top[0], False
    numbers = [retry_number(row.get("retryCount")) for row in top[:2]]
    if missing_execution_id > 0 or missing_retry_count > 0 or len(numbers) < 2 or None in numbers or \
            numbers[0] == numbers[1]:
        return None, True
    return top[0], False


def build_instance_graph(*, source_id: str | None, transaction: Mapping[str, Any] | None,
                         processes: Iterable[Mapping[str, Any]], messages: Iterable[Mapping[str, Any]],
                         processes_truncated: bool, messages_truncated: bool, generated_at: str) -> dict[str, Any]:
    """TS `buildInstanceGraph`와 같은 규칙·같은 순서로 `{graph, nodes, edges}`를 만든다."""
    processes, messages = list(processes), list(messages)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tx = transaction
    tx_node_id = None
    if tx:
        tx_node_id = transaction_id(tx["transactionId"]) if _present(tx.get("transactionId")) else \
            unkeyed_id(tx["observationId"]) if tx.get("observationId") is not None else None
    if tx and tx_node_id:
        nodes.append({"id": tx_node_id, "kind": "transaction", "attrs": {
            "observationId": tx.get("observationId"), "revisionNo": tx.get("revisionNo"),
            "transactionId": tx.get("transactionId"), "interfaceId": tx.get("interfaceId"),
            "channelId": tx.get("channelId"), "status": tx.get("status"), "responseCode": tx.get("responseCode"),
            "processTimeMs": tx.get("processTimeMs"), "processCount": tx.get("processCount"),
            "unkeyed": not _present(tx.get("transactionId")),
            "retries": sorted(tx.get("retries") or [], key=_by_retry),
            "retriesTruncated": tx.get("retriesTruncated") is True,
            "verification": "ambiguous" if tx.get("ambiguous") else None}})

    # PROCESS: (거래 ID, Process ID)마다 노드 1개(재시도 묶음). 구성 요소가 없음이면 행마다 unkeyed 노드.
    groups: dict[str, dict[str, Any]] = {}
    for row in processes:
        keyed = _present(row.get("transactionId")) and _present(row.get("processId"))
        node_id = process_id(row["transactionId"], row["processId"]) if keyed else unkeyed_id(row["observationId"])
        group = groups.setdefault(node_id, {"id": node_id, "keyed": keyed, "transactionId": row.get("transactionId"),
                                            "processId": row.get("processId"), "attempts": [], "ambiguous": False,
                                            "dependsOnValues": []})
        group["attempts"].append(row)
    attempt_owner: dict[str, dict[str, Any]] = {}
    for group in groups.values():
        group["attempts"].sort(key=_by_retry)
        # 실행마다 최신 리비전으로 줄인 뒤에도 같은 재시도 번호(또는 번호 없음)가 2건 이상이면 대표를 고르지 않는다.
        group["ambiguous"] = retry_group_ambiguous(group["attempts"])
        # 없음 값(NULL·""·\N, 이 열만 NONE도)은 하나로 본다 = 시작 단계. 처음 나온 순서를 지킨다.
        group["dependsOnValues"] = list(dict.fromkeys(
            attempt.get("dependsOn") if _present(attempt.get("dependsOn"), DEPEND_PROCESS_ID) else None
            for attempt in group["attempts"]))
        for attempt in group["attempts"]:
            attempt_owner[attempt["observationId"]] = group

    missing: dict[str, dict[str, Any]] = {}

    def add_missing(referencing_node_id: str, process_id_value: str) -> str:
        node_id = missing_id(referencing_node_id, process_id_value)
        if node_id not in missing:
            missing[node_id] = {"id": node_id, "kind": "missing", "attrs": {
                "processId": process_id_value, "reason": "not_loaded" if processes_truncated else "not_logged"}}
        return node_id

    # 선행: from = 선행(DEPEND_PROCESS_ID가 가리키는 묶음), to = 후행(그 열을 가진 묶음).
    precedes: list[dict[str, Any]] = []
    self_dependency: set[str] = set()
    for successor in groups.values():
        if not successor["keyed"]:
            continue
        disagree = len(successor["dependsOnValues"]) > 1
        for depends_on in successor["dependsOnValues"]:
            if depends_on is None:
                continue
            if depends_on == successor["processId"]:
                self_dependency.add(successor["id"])
                continue
            predecessor = groups.get(process_id(successor["transactionId"], depends_on))
            if predecessor:
                verification = "ambiguous" if disagree or predecessor["ambiguous"] or successor["ambiguous"] \
                    else "verified"
                precedes.append({"from": predecessor, "to": successor, "edge": {
                    "id": edge_id("precedes", predecessor["id"], successor["id"]), "kind": "precedes",
                    "from": predecessor["id"], "to": successor["id"], "verification": verification, "evidence": None}})
            else:
                missing_node = add_missing(successor["id"], depends_on)
                precedes.append({"from": None, "to": successor, "edge": {
                    "id": edge_id("precedes", missing_node, successor["id"]), "kind": "precedes",
                    "from": missing_node, "to": successor["id"],
                    "verification": "ambiguous" if disagree or successor["ambiguous"] else "unmatched",
                    "evidence": None}})
    outgoing: dict[str, set[str]] = {}
    for item in precedes:
        if item["from"]:
            outgoing.setdefault(item["from"]["id"], set()).add(item["to"]["id"])

    for group in groups.values():
        last = group["attempts"][-1]
        values = group["dependsOnValues"]
        nodes.append({"id": group["id"], "kind": "process", "attrs": {
            "transactionId": group["transactionId"], "processId": group["processId"],
            "dependsOn": values[0] if len(values) == 1 else None,
            "status": None if group["ambiguous"] else last.get("status"),
            "responseCode": None if group["ambiguous"] else last.get("responseCode"),
            "focus": any(attempt.get("focus") is True for attempt in group["attempts"]),
            "verification": "ambiguous" if group["ambiguous"] else None, "unkeyed": not group["keyed"],
            "branch": len(outgoing.get(group["id"], ())) >= 2, "selfDependency": group["id"] in self_dependency,
            "attempts": [{"observationId": a["observationId"], "executionId": a.get("executionId"),
                          "revisionNo": a.get("revisionNo"), "retryCount": a.get("retryCount"),
                          "status": a.get("status"), "responseCode": a.get("responseCode"),
                          "dependsOn": a.get("dependsOn"), "totalCount": a.get("totalCount"),
                          "focus": a.get("focus") is True, "decisionId": a.get("decisionId"),
                          "grade": a.get("grade")} for a in group["attempts"]]}})
    if tx and tx_node_id:
        for group in groups.values():
            # 대표 행이 없으면(모호) 어느 시도도 검증된 연결로 보지 않는다.
            verified = tx.get("observationId") is not None and all(
                attempt.get("transactionObservationId") == tx.get("observationId") for attempt in group["attempts"])
            edges.append({"id": edge_id("contains", tx_node_id, group["id"]), "kind": "contains", "from": tx_node_id,
                          "to": group["id"], "verification": "verified" if verified else "not_checked",
                          "evidence": None})
    edges.extend(item["edge"] for item in precedes)

    # MESSAGE: 실행 ID로 노드 1개. FK가 그래프 안 PROCESS 시도를 가리키면 verified, 아니면 같은 거래의
    # Process ID 문자열(not_checked), 그 Process가 없으면 missing 노드(unmatched).
    message_nodes: list[dict[str, Any]] = []
    contains_messages: list[dict[str, Any]] = []
    messages_of: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for row in messages:
        node_id = message_id(row["executionId"]) if _present(row.get("executionId")) else \
            unkeyed_id(row["observationId"])
        message_nodes.append({"id": node_id, "kind": "message", "attrs": {
            "observationId": row["observationId"], "executionId": row.get("executionId"),
            "revisionNo": row.get("revisionNo"), "messageId": row.get("messageId"), "processId": row.get("processId"),
            "direction": row.get("direction"), "status": row.get("status"), "dataSize": row.get("dataSize"),
            "messageIndex": row.get("messageIndex"), "unkeyed": not _present(row.get("executionId"))}})
        if row.get("processObservationId") is not None and row["processObservationId"] in attempt_owner:
            owner, verification = attempt_owner[row["processObservationId"]], "verified"
        elif _present(row.get("processId")) and _present(row.get("transactionId")):
            owner, verification = groups.get(process_id(row["transactionId"], row["processId"])), "not_checked"
        else:
            continue  # 묶을 값 없음 → 포함 간선 없음
        if owner:
            contains_messages.append({"id": edge_id("contains", owner["id"], node_id), "kind": "contains",
                                      "from": owner["id"], "to": node_id, "verification": verification,
                                      "evidence": None})
            messages_of.setdefault(owner["id"], []).append((node_id, row))
        else:
            missing_node = add_missing(node_id, row["processId"])
            contains_messages.append({"id": edge_id("contains", missing_node, node_id), "kind": "contains",
                                      "from": missing_node, "to": node_id, "verification": "unmatched",
                                      "evidence": None})
    nodes.extend(missing.values())
    nodes.extend(message_nodes)
    edges.extend(contains_messages)

    # 그래프 거래 ID: TRANSACTION 행 값, 그 행이 없거나 값이 없음이면 판정 PROCESS 값.
    focus_row = next((row for row in processes if row.get("focus") is True), None)
    graph_id = next((value for value in ((tx or {}).get("transactionId"),
                                         focus_row.get("transactionId") if focus_row else None) if _present(value)),
                    None)
    excluded = graph_id is not None and EXCLUDED_TRANSACTION_SUFFIX.search(graph_id) is not None
    for item in precedes:
        edge, from_group, to_group = item["edge"], item["from"], item["to"]
        if edge["verification"] != "verified" or not from_group:
            edge["evidence"] = {"handoff": "undetermined", "reason": "precedes_not_verified"}
            continue
        if messages_truncated:
            edge["evidence"] = {"handoff": "undetermined", "reason": "messages_truncated"}
            continue
        outs = [m for m in messages_of.get(from_group["id"], []) if m[1].get("direction") == "OUT"]
        ins = [m for m in messages_of.get(to_group["id"], []) if m[1].get("direction") == "IN"]
        if not outs:
            edge["evidence"] = {"handoff": "undetermined", "reason": "no_out_message"}
            continue
        results = []
        for out_id, out_row in outs:
            reason = None
            matched = [i for i in ins if i[1].get("messageId") == out_row.get("messageId")] \
                if _present(out_row.get("messageId")) else []
            if not ins:
                result, reason, in_messages = "undetermined", "no_in_message", []
            elif not _present(out_row.get("messageId")):
                result, reason, in_messages = "undetermined", "missing_message_id", [i[0] for i in ins]
            elif matched:
                result, in_messages = "match", [i[0] for i in matched]
            elif len(outs) == 1 and len(ins) == 1:
                in_messages = [ins[0][0]]
                if not _present(ins[0][1].get("messageId")):
                    result, reason = "undetermined", "missing_message_id"
                elif excluded:
                    result, reason = "undetermined", "excluded_transaction_suffix"
                else:
                    result = "mismatch"
            else:
                result, reason, in_messages = "undetermined", "multiple_messages", [i[0] for i in ins]
            results.append((result, reason))
            edges.append({"id": hands_off_id(from_group["id"], to_group["id"], out_id), "kind": "hands_off",
                          "from": from_group["id"], "to": to_group["id"], "verification": edge["verification"],
                          "evidence": {"result": result, "reason": reason, "outMessage": out_id,
                                       "inMessages": in_messages}})
        summary = next((r for r in results if r[0] == "match"), None) or \
            next((r for r in results if r[0] == "mismatch"), None) or results[0]
        edge["evidence"] = {"handoff": summary[0], "reason": summary[1]}

    return {"graph": {"layer": "instance", "id": graph_id, "version": None, "sourceId": source_id,
                      "snapshotId": None, "period": None, "ruleSetRevision": None, "generatedAt": generated_at},
            "nodes": nodes, "edges": edges}
