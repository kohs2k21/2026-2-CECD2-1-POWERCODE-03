"""룰 세트 hash v3 독립 구현(Python, rule-set.md v3.3) — rule-set-v3.test.mjs가 Node 구현과 같은 문자열·hash인지 비교한다.

입력(표준 입력 JSON): 저장 형식 스냅샷 배열 [{hardRules, softRules, relationRules?, protocolRules?}].
출력(표준 출력 JSON): [{canonical, sha256}]. 직렬화는 계약 그대로 json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
Detector 코드를 읽지 않고 계약 문장만으로 만든 구현이라 두 구현이 같은 값을 내면 계약 해석이 같다는 뜻이다.
"""
import hashlib
import json
import sys
from decimal import Decimal


def threshold(value):
    text = format(Decimal(str(value)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def severity(value):
    if isinstance(value, dict) and isinstance(value.get("byCode"), dict):
        out = {"byCode": [[code, value["byCode"][code]] for code in sorted(value["byCode"])]}
        if "default" in value:
            out["default"] = value["default"]
        return out
    return value


def path_key(channels):
    return ":".join(channel.replace("%", "%25").replace(":", "%3A") for channel in channels)


def soft_scope(scope):
    if scope is None:
        return None
    kept = {key: scope[key] for key in ("processId", "adapterType", "processHubId") if key in scope}
    return kept or None


def relation_scope(kind, scope):
    if scope is None:
        return None
    if kind == "required_step":
        return {"interfaceId": scope["interfaceId"]}
    return {"adapterType": scope["adapterType"]} if "adapterType" in scope else None


def canonical(snapshot):
    by_id = lambda item: item["id"]
    return {
        "hardRules": [{"id": r["id"], "enabled": r["enabled"], "severity": severity(r["severity"])}
                      for r in sorted(snapshot["hardRules"], key=by_id)],
        "softRules": [{"id": r["id"], "field": r["field"], "operator": r["operator"], "threshold": threshold(r["threshold"]),
                       "severity": r["severity"], "enabled": r["enabled"], "scope": soft_scope(r.get("scope"))}
                      for r in sorted(snapshot["softRules"], key=by_id)],
        "relationRules": [{"id": r["id"], "kind": r["kind"], "scope": relation_scope(r["kind"], r["scope"]),
                           "from": {"processId": r["from"]["processId"]},
                           "to": None if r["to"] is None else {"processId": r["to"]["processId"]},
                           "match": None if r["match"] is None else {k: r["match"][k] for k in ("from", "to", "key")},
                           "severity": r["severity"], "enabled": r["enabled"]}
                          for r in sorted(snapshot.get("relationRules", []), key=by_id)],
        "protocolRules": [{"id": r["id"], "interfaceId": r["interfaceId"],
                           "paths": sorted((p["channels"] for p in r["paths"]), key=path_key),
                           "severity": r["severity"], "enabled": r["enabled"]}
                          for r in sorted(snapshot.get("protocolRules", []), key=by_id)],
    }


def main():
    snapshots = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    out = []
    for snapshot in snapshots:
        text = json.dumps(canonical(snapshot), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        out.append({"canonical": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()})
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=True).encode("ascii"))


if __name__ == "__main__":
    main()
