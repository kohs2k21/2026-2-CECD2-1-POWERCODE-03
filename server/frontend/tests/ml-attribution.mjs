import assert from "node:assert/strict";
// 판정 상세 머신러닝 모델 기여도: attribution 읽기(키 없음·skipped·failed·형식 어긋남), 문장 틀·반올림·조사·이름표·scale 1 규칙, 방법 표 아래 표시.
// 문장 규칙은 detection-decision.md "6.1 ML 기여도" 절, 수치는 그 절의 예(설명용 수치)를 쓴다.
const { readAttribution } = await import("../src/features/analysis/data/attribution.ts");
const { readEvidenceV4 } = await import("../src/features/analysis/data/evidence.ts");
const text = await import("../src/features/analysis/attributionText.ts");
const { attributionSentences, attributionTableRows, roundHalfUp, percentText, scoreDigits, topic, objectParticle, toward, expandExponent, groupDigits } = text;
const React = (await import("react")).default;
globalThis.React = React; // 컴포넌트가 JSX 런타임으로 전역 React를 쓴다(다른 시험과 같음)
const { renderToStaticMarkup } = await import("react-dom/server");
const { MlAttribution } = await import("../src/features/analysis/components/MlAttribution.tsx");

const player = (playerId, features, actual, baseline, phi, share, extra = {}) => ({ playerId, features, actual, baseline, phi, share, thresholdGapShare: null, ...extra });
const T = player("total_count", ["total_count"], { total_count: "120" }, { total_count: 100 }, 0.015, 0.0484);
const E = player("error_count", ["error_count"], { error_count: "15" }, { error_count: 0 }, 0.175, 0.5645);
const L = player("elapsed_ms", ["elapsed_ms"], { elapsed_ms: "4200" }, { elapsed_ms: 800 }, 0.12, 0.3871);
const base = (over = {}) => ({
  version: "baseline-shapley-v1", status: "succeeded", baselineKind: "train_median_robust_scaler_center", grouping: "source-field-union-v1",
  scoreActual: 0.71, scoreBaseline: 0.4, threshold: "0.62", direction: "higher_is_anomaly", baselineWarning: false, efficiencyError: 0,
  players: [T, E, L], topK: ["error_count", "elapsed_ms", "total_count"],
  counterfactual: [
    { replaced: ["error_count"], scoreAfter: 0.51, warningAfterReplacement: false },
    { replaced: ["error_count", "elapsed_ms"], scoreAfter: 0.41, warningAfterReplacement: false },
    { replaced: ["error_count", "elapsed_ms", "total_count"], scoreAfter: 0.4, warningAfterReplacement: false },
  ],
  clearedAtK: 1, pins: {}, computeMs: 0.8, ...over,
});
const read = (attribution) => readAttribution({ methodId: "ml:iforest", category: "ml", attribution });
const sentences = (over) => {
  const a = read(base(over));
  assert.equal(a.status, "succeeded", JSON.stringify(over));
  return attributionSentences(a);
};
const LIMIT = "기여도는 모델이 점수를 매긴 방식에 대한 분석이며 실제 장애 원인이 아닙니다. 원인은 원천 시스템 운영 로그로 확인하세요.";

// 1) 예시 문장 글자 그대로(이름표: 단위 미확인 건수 변수는 원래 필드 이름, 값이 함께 나오는 곳에서만 `(단위 미확인)`)
const example = sentences();
assert.equal(example.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT 56%, 처리 시간 39%, 처리량(TOTAL_COUNT) 5% 순입니다.");
assert.equal(example.counterfactual, "ERROR_COUNT(단위 미확인)만 학습 데이터 중앙값(0)으로 바꾸면 원점수가 판정선보다 낮아져(값 0.510) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.");
assert.deepEqual([example.shareNull, example.negative, example.groups, example.baselineWarning, example.limitation], [null, null, [], null, LIMIT]);
assert.deepEqual(attributionTableRows(read(base())).map((r) => [r.members.map((m) => [m.name, m.actual, m.baseline]), r.share]), [
  [[["ERROR_COUNT", "15(단위 미확인)", "0"]], "56%"],
  [[["처리 시간", "4,200 ms", "800 ms"]], "39%"],
  [[["처리량(TOTAL_COUNT)", "120(단위 미확인)", "100"]], "5%"],
], "table: (단위 미확인) after the actual value, unit ms, share % half-up; no 건");

// 2) topK 2개·1개(뿐)·0개
assert.equal(sentences({ topK: ["error_count", "elapsed_ms"], counterfactual: base().counterfactual.slice(0, 2) }).summary,
  "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT 56%, 처리 시간 39% 순입니다.");
const one = sentences({ topK: ["error_count"], counterfactual: base().counterfactual.slice(0, 1) });
assert.equal(one.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT뿐입니다.", "single positive player: no 100% 순입니다");
const none = sentences({ topK: [], counterfactual: [], clearedAtK: null });
assert.deepEqual([none.summary, none.counterfactual, none.shareNull, none.limitation], ["원점수를 이상 쪽으로 움직인 변수를 찾지 못했습니다.", null, null, LIMIT]);

// 3) share null: 비율 없이 이름만 + 안내
const nullShare = sentences({ players: [{ ...T, share: null }, { ...E, share: null }, { ...L, share: null }] });
assert.equal(nullShare.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT, 처리 시간, 처리량(TOTAL_COUNT) 순입니다.");
assert.equal(nullShare.shareNull, "학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다.");
assert.equal(attributionTableRows(read(base({ players: [{ ...T, share: null }, E, L] })))[2].share, null);

// 4) 음수 φ: 주석(기존 틀 그대로), 비율 합 보정 없음
const neg = sentences({ players: [{ ...T, phi: -0.02, share: -0.0645 }, { ...E, share: 0.6774 }, { ...L, share: 0.4516 }], topK: ["error_count", "elapsed_ms"],
  counterfactual: base().counterfactual.slice(0, 2) });
assert.equal(neg.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT 68%, 처리 시간 45% 순입니다.", "68+45 > 100 kept");
assert.equal(neg.negative, "처리량(TOTAL_COUNT)은 원점수를 정상 쪽으로 움직였습니다. 그래서 다른 변수 비율 합이 100%를 넘을 수 있습니다.");

// 5) 묶음 참가자 + scale 1(구성원별): scaleIsUnit 없음 / 일부 true / 묶음 안 일부 true
const G = player("group:total_count", ["total_count", "error_count", "error_ratio"], { total_count: "120", error_count: "15", error_ratio: "0.125" },
  { total_count: 100, error_count: 0, error_ratio: 0 }, 0.2, 0.6452);
const R = player("retry_count", ["retry_count"], { retry_count: "4" }, { retry_count: 0 }, 0.08, 0.2581);
const GROUP_MEMBERS = "처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 중앙값 100, ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0)";
const groupCase = (g, r = R) => sentences({ players: [g, r, L], topK: ["group:total_count", "elapsed_ms", "retry_count"],
  counterfactual: [
    { replaced: ["group:total_count"], scoreAfter: 0.5, warningAfterReplacement: false },
    { replaced: ["group:total_count", "elapsed_ms"], scoreAfter: 0.45, warningAfterReplacement: false },
    { replaced: ["group:total_count", "elapsed_ms", "retry_count"], scoreAfter: 0.4, warningAfterReplacement: false },
  ] });
const grouped = groupCase(G, { ...R, share: 0.0968 });
assert.equal(grouped.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 중앙값 100, "
  + "ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0) 65%, 처리 시간 39%, RETRY_COUNT 10% 순입니다.");
assert.equal(grouped.counterfactual, "처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 중앙값 100, "
  + "ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0)만 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.500) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.");
assert.deepEqual(grouped.groups, ["처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값은 같은 원천 값을 쓰는 변수라 한 묶음으로 계산했습니다."]);
const scaled = groupCase({ ...G, scaleIsUnit: { total_count: true, error_count: false, error_ratio: "yes" } }, { ...R, share: 0.0968, scaleIsUnit: { retry_count: true } });
assert.equal(scaled.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 평소 값과 다름, "
  + "ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0) 65%, 처리 시간 39%, RETRY_COUNT 평소 값과 다름 10% 순입니다.",
  "scale 1 only for true members; non-bool ignored; single scale-1 player = name, 평소 값과 다름, share");
const scaledRows = attributionTableRows(read(base({ players: [{ ...G, scaleIsUnit: { total_count: true } }, R, L], topK: ["group:total_count", "retry_count"],
  counterfactual: [{ replaced: ["group:total_count"], scoreAfter: 0.7, warningAfterReplacement: true }, { replaced: ["group:total_count", "retry_count"], scoreAfter: 0.65, warningAfterReplacement: true }], clearedAtK: null })));
assert.deepEqual(scaledRows.map((r) => r.members.map((m) => [m.name, m.actual, m.baseline])), [
  [["처리량(TOTAL_COUNT)", "120(단위 미확인)", "평소 값과 다름"], ["ERROR_COUNT", "15(단위 미확인)", "0"], ["오류 비율 값", "0.125", "0"]],
  [["RETRY_COUNT", "4(단위 미확인)", "0"]],
], "group members listed with their own actual/median; RETRY_COUNT label");
// 반사실 scale 1: 단독은 `({baseline})` 생략(값이 없으니 `(단위 미확인)`도 없음), 묶음은 true인 구성원만 `평소 값과 다름`
assert.equal(sentences({ players: [T, { ...E, scaleIsUnit: { error_count: true } }, L] }).counterfactual,
  "ERROR_COUNT만 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.510) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.");
assert.equal(groupCase({ ...G, scaleIsUnit: { total_count: true } }).counterfactual, "처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값(처리량(TOTAL_COUNT) 120(단위 미확인) · 평소 값과 다름, "
  + "ERROR_COUNT 15(단위 미확인) · 중앙값 0, 오류 비율 값 0.125 · 중앙값 0)만 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.500) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.");
// 큰 값의 천 단위 쉼표(자리 값 그대로)
assert.equal(attributionTableRows(read(base({ players: [{ ...T, actual: { total_count: "10485760" }, baseline: { total_count: 10485760 } }, E, L] })))[2].members[0].actual, "10,485,760(단위 미확인)");

// 지수 표기 실제값은 문자열로 펼쳐 보인다(반올림 없음), 숫자가 아니면 원문 그대로
assert.deepEqual(["9.5E+3", "1E-7", "-1.25E+2", "0E-10", "1.23456789012345678901E+25", "120", "abc", "1E+x", "-0.5"].map(groupDigits),
  ["9,500", "0.0000001", "-125", "0.0000000000", "12,345,678,901,234,567,890,100,000", "120", "abc", "1E+x", "-0.5"]);
assert.equal(expandExponent("1.5E-3"), "0.0015");
assert.equal(attributionTableRows(read(base({ players: [T, { ...E, actual: { error_count: "9.5E+3" } }, L] })))[0].members[0].actual, "9,500(단위 미확인)");
// share null이 일부 참가자만(계약상 없음): 그 참가자만 비율 생략 + 안내
const mixed = sentences({ players: [T, { ...E, share: null }, L] });
assert.equal(mixed.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT, 처리 시간 39%, 처리량(TOTAL_COUNT) 5% 순입니다.");
assert.equal(mixed.shareNull, "학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다.");

// 6) 반사실: k=2 해소, 유지, lower_is_anomaly
const k2 = sentences({ counterfactual: [
  { replaced: ["error_count"], scoreAfter: 0.63, warningAfterReplacement: true },
  { replaced: ["error_count", "elapsed_ms"], scoreAfter: 0.41, warningAfterReplacement: false },
  { replaced: ["error_count", "elapsed_ms", "total_count"], scoreAfter: 0.4, warningAfterReplacement: false },
], clearedAtK: 2 });
assert.equal(k2.counterfactual, "ERROR_COUNT·처리 시간을 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.410) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. "
  + "ERROR_COUNT만 바꾸면 원점수는 0.630으로 여전히 판정선 기준 이상 범위에 남습니다.");
const kept = sentences({ counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [0.68, 0.65, 0.641][i], warningAfterReplacement: true })), clearedAtK: null });
assert.equal(kept.counterfactual, "상위 3개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 이상 범위에 남습니다(값 0.641).");
const lower = sentences({ direction: "lower_is_anomaly", scoreActual: -0.71, threshold: "-0.62",
  counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [-0.51, -0.41, -0.4][i] })) });
assert.equal(lower.summary, "머신러닝 모델 원점수(값 -0.710, 판정선 -0.620)를 이상 쪽으로 내린 변수는 ERROR_COUNT 56%, 처리 시간 39%, 처리량(TOTAL_COUNT) 5% 순입니다.");
assert.ok(lower.counterfactual.includes("판정선보다 높아져(값 -0.510)"));
// 기준값 경고
assert.equal(sentences({ baselineWarning: true }).baselineWarning, "학습 데이터 중앙값에서도 원점수가 판정선을 넘어 비율을 해석할 때 주의가 필요합니다.");

// 7) 자릿수 늘리기: 셋째 자리에서 같은 글자 → 넷째 자리에서 갈림 / 여섯째 자리까지 같으면 여섯째
const tie4 = sentences({ scoreActual: 0.6203 });
assert.ok(tie4.summary.startsWith("머신러닝 모델 원점수(값 0.6203, 판정선 0.6200)"), tie4.summary);
assert.ok(tie4.counterfactual.includes("(값 0.5100)"), "counterfactual uses the same digits");
const cfTie = sentences({ counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [0.61998, 0.41, 0.4][i] })) });
assert.ok(cfTie.summary.includes("(값 0.71000, 판정선 0.62000)") && cfTie.counterfactual.includes("(값 0.61998)"), "counterfactual ties at 3·4 digits → all values use 5 digits");
assert.equal(scoreDigits({ scoreActual: 0.61998, threshold: "0.62", counterfactual: [] }), 5);
const tie6 = sentences({ scoreActual: 0.6200001 });
assert.ok(tie6.summary.startsWith("머신러닝 모델 원점수(값 0.620000, 판정선 0.620000)"), "max 6 digits even when still equal");
// half-up(10진 문자열 기준, 2진 오차 없음)
assert.deepEqual([roundHalfUp("0.0005", 3), roundHalfUp("2.675", 2), roundHalfUp("-0.0004", 3), roundHalfUp("0.62", 3)], ["0.001", "2.68", "0.000", "0.620"]);
assert.deepEqual([percentText(0.125), percentText(0.0484), percentText(0.005), percentText(1.04)], ["13%", "5%", "1%", "104%"]);

// 8) 조사(받침, 숫자는 읽는 소리, 영문은 끝 글자 L·M·N·NG만 받침)
assert.deepEqual(["0.630", "0.512", "0.517", "0.516", "10", "1,000", "4,200 ms", "0.500"].map(toward),
  ["0.630으로", "0.512로", "0.517로", "0.516으로", "10으로", "1,000으로", "4,200 ms로", "0.500으로"]);
assert.deepEqual(["처리 시간", "ERROR_COUNT", "오류 비율 값", "RETRY_COUNT", "변수", "처리량(TOTAL_COUNT)", "SUCCESS_COUNT·처리량(TOTAL_COUNT)"].map(topic),
  ["처리 시간은", "ERROR_COUNT는", "오류 비율 값은", "RETRY_COUNT는", "변수는", "처리량(TOTAL_COUNT)은", "SUCCESS_COUNT·처리량(TOTAL_COUNT)은"], "particle follows the word before a trailing parenthesis");
assert.deepEqual(["처리 시간", "ERROR_COUNT", "LEVEL", "SCORE_M", "BAR", "SONG", "ERROR_COUNT(단위 미확인)"].map(objectParticle), ["을", "를", "을", "을", "를", "을", "를"],
  "Latin: only L·M·N·NG carry a final consonant; trailing parentheses are skipped");

// 9) 상태: 키 없음·skipped·failed·형식 어긋남
assert.deepEqual(readAttribution({ methodId: "ml:iforest" }), { status: "absent" });
assert.deepEqual(read({ version: "baseline-shapley-v1", status: "skipped", reason: "too_many_players" }), { status: "skipped", reason: "too_many_players" });
assert.deepEqual(read({ version: "baseline-shapley-v1", status: "skipped", reason: "new_reason" }), { status: "skipped", reason: null }, "unknown code is not shown");
assert.deepEqual(read({ version: "baseline-shapley-v1", status: "failed", failureCode: "score_not_reproduced", computeMs: 1 }), { status: "failed", failureCode: "score_not_reproduced" });
for (const bad of [null, "x", { status: "succeeded" }, { version: "baseline-shapley-v2", status: "skipped", reason: "not_anomalous" }, { version: "baseline-shapley-v1", status: "done" },
  base({ scoreActual: "0.71" }), base({ threshold: 0.62 }), base({ topK: ["missing"] }), base({ clearedAtK: 2 }), base({ counterfactual: [] }),
  base({ players: [{ ...T, actual: { total_count: 120 } }, E, L] }), base({ players: [{ ...T, share: undefined }, E, L] }),
  base({ counterfactual: base().counterfactual.map((c) => ({ ...c, replaced: ["error_count"] })) })])
  assert.equal(read(bad).status, "unreadable", JSON.stringify(bad).slice(0, 80));
assert.equal(read(base({ players: [{ ...T, scaleIsUnit: "x" }, E, L] })).status, "succeeded", "malformed scaleIsUnit is ignored, not unreadable");

// 11) 보충 규칙: 양수 1개 + scale 1, 음수 여럿 쉼표, tol(`max(1e-9, 1e-9·|scoreActual|)`) 경계
const oneScaled = sentences({ players: [T, { ...E, scaleIsUnit: { error_count: true } }, L], topK: ["error_count"], counterfactual: base().counterfactual.slice(0, 1) });
assert.equal(oneScaled.summary, "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 ERROR_COUNT뿐입니다(평소 값과 다름).");
const twoNeg = sentences({ players: [{ ...T, phi: -0.01 }, E, { ...L, phi: -0.02 }], topK: ["error_count"], counterfactual: base().counterfactual.slice(0, 1) });
assert.equal(twoNeg.negative, "처리량(TOTAL_COUNT), 처리 시간은 원점수를 정상 쪽으로 움직였습니다. 그래서 다른 변수 비율 합이 100%를 넘을 수 있습니다.");
assert.equal(sentences({ players: [{ ...T, phi: -1e-10 }, E, L] }).negative, null, "phi within tol (1e-9) is not negative");
assert.equal(sentences({ players: [{ ...T, phi: -1.5e-9 }, E, L] }).negative?.startsWith("처리량(TOTAL_COUNT)은"), true, "phi below −tol is negative");
const bigScore = base({ scoreActual: 2000, threshold: "1500", counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [1400, 1300, 1200][i] })) });
assert.equal(attributionSentences(read({ ...bigScore, players: [{ ...T, phi: -1.5e-6 }, E, L] })).negative, null, "tol scales with |scoreActual| (2e-6)");
assert.ok(attributionSentences(read({ ...bigScore, players: [{ ...T, phi: -2.5e-6 }, E, L] })).negative);
// tol의 score = 같은 ML 방법 결과의 `score`(있으면). 없거나 숫자가 아니면 scoreActual로 대체(위 두 건)
const withMethodScore = (score, phi) => readAttribution({ methodId: "ml:iforest", category: "ml", score, attribution: base({ players: [{ ...T, phi }, E, L] }) });
assert.equal(withMethodScore(2000, -1.5e-6).methodScore, 2000);
assert.equal(attributionSentences(withMethodScore(2000, -1.5e-6)).negative, null, "method score 2000 → tol 2e-6 even though scoreActual is 0.71");
assert.ok(attributionSentences(withMethodScore("2000", -1.5e-6)).negative, "non-number method score → falls back to scoreActual (tol 1e-9)");
assert.equal(withMethodScore(null, -1.5e-6).methodScore, null);
// k=3 해소, 묶음이 마지막인 k=2(조사는 괄호 앞 이름)
const k3 = sentences({ counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [0.66, 0.63, 0.5][i], warningAfterReplacement: i < 2 })), clearedAtK: 3 });
assert.equal(k3.counterfactual, "ERROR_COUNT·처리 시간·처리량(TOTAL_COUNT)을 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.500) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. "
  + "ERROR_COUNT만 바꾸면 원점수는 0.660으로 여전히 판정선 기준 이상 범위에 남습니다.");
const groupLast = sentences({ players: [L, G], topK: ["elapsed_ms", "group:total_count"], counterfactual: [
  { replaced: ["elapsed_ms"], scoreAfter: 0.65, warningAfterReplacement: true },
  { replaced: ["elapsed_ms", "group:total_count"], scoreAfter: 0.4, warningAfterReplacement: false },
], clearedAtK: 2 });
assert.equal(groupLast.counterfactual, "처리 시간·" + GROUP_MEMBERS + "을 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.400) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. "
  + "처리 시간만 바꾸면 원점수는 0.650으로 여전히 판정선 기준 이상 범위에 남습니다.");
// 보이는 반사실 점수만 자릿수 늘리기 대상(해소 k=1이면 k=2·3 점수는 판정선과 같은 글자여도 무시)
assert.ok(sentences({ counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [0.51, 0.6201, 0.6199][i] })) }).summary.includes("(값 0.710, 판정선 0.620)"));
// 판정선·점수가 지수 표기여도 펼친 뒤 반올림
const tiny = sentences({ scoreActual: 2e-5, threshold: "1e-05", counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [5e-6, 4e-6, 3e-6][i] })) });
assert.ok(tiny.summary.startsWith("머신러닝 모델 원점수(값 0.000020, 판정선 0.000010)"), tiny.summary);
assert.ok(tiny.counterfactual.includes("(값 0.000005)"));
assert.deepEqual([roundHalfUp("1e-05", 5), roundHalfUp("6.2E-1", 3), roundHalfUp("abc", 3)], ["0.00001", "0.620", "abc"]);
// 지수 상한: |지수| > 400이면 원문 그대로, 어떤 입력에도 예외 없음
assert.equal(expandExponent("1E+400").length, 401);
for (const raw of ["1E+401", "1E-401", "1E+99999999999999999999", "--1E+3", "", "1.2.3E+4"]) assert.equal(groupDigits(raw), raw, `kept ${raw}`);
// 예상 못 한 예외(판독을 거치지 않은 잘못된 객체)도 화면을 내리지 않고 `기여도 표시 불가`
const guarded = renderToStaticMarkup(React.createElement(MlAttribution, { attribution: { ...read(base()), topK: ["nobody"] } }));
assert.ok(guarded.includes('data-attribution-status="unreadable"') && guarded.includes("기여도 표시 불가"), guarded);
// 실제 마크업: 묶음·안내·비율 없음
const markup = renderToStaticMarkup(React.createElement(MlAttribution, { attribution: read(base({ players: [{ ...G, share: null }, { ...L, share: null }], topK: ["group:total_count", "elapsed_ms"],
  counterfactual: [{ replaced: ["group:total_count"], scoreAfter: 0.7, warningAfterReplacement: true }, { replaced: ["group:total_count", "elapsed_ms"], scoreAfter: 0.66, warningAfterReplacement: true }],
  clearedAtK: null, baselineWarning: true })) }));
const lines = [...markup.matchAll(/data-attribution="([a-z]+)">([^<]*)</g)].map((m) => [m[1], m[2]]);
assert.deepEqual(lines, [
  ["summary", "머신러닝 모델 원점수(값 0.710, 판정선 0.620)를 이상 쪽으로 올린 변수는 " + GROUP_MEMBERS + ", 처리 시간 순입니다."],
  ["counterfactual", "상위 2개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 이상 범위에 남습니다(값 0.660)."],
  ["note", "학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다."],
  ["note", "처리량(TOTAL_COUNT)·ERROR_COUNT·오류 비율 값은 같은 원천 값을 쓰는 변수라 한 묶음으로 계산했습니다."],
  ["note", "학습 데이터 중앙값에서도 원점수가 판정선을 넘어 비율을 해석할 때 주의가 필요합니다."],
  ["limitation", LIMIT],
]);
assert.equal([...markup.matchAll(/data-attribution-player="true"/g)].length, 2);
assert.ok(markup.includes("<div>120(단위 미확인)</div><div>15(단위 미확인)</div><div>0.125</div>") && markup.includes(">—<"), "group members on their own lines; null share = —");

// 12) 판정선과 같은 점수(|점수 − 판정선| ≤ tol): 숫자 대신 `판정선과 같음`, 자릿수 늘리기에서 뺀다. 문장 갈래는 저장된 warningAfterReplacement 그대로
const keptCf = (scores) => base().counterfactual.map((c, i) => ({ ...c, scoreAfter: scores[i], warningAfterReplacement: true }));
const sameActual = sentences({ scoreActual: 0.62, counterfactual: keptCf([0.61, 0.5, 0.4]), clearedAtK: null });
assert.ok(sameActual.summary.startsWith("머신러닝 모델 원점수(판정선과 같음, 판정선 0.620)를 이상 쪽으로 올린 변수는 "), sameActual.summary);
assert.equal(sameActual.counterfactual, "상위 3개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 이상 범위에 남습니다(값 0.400).");
const sameKept = sentences({ topK: ["error_count"], counterfactual: [{ replaced: ["error_count"], scoreAfter: 0.62, warningAfterReplacement: true }], clearedAtK: null });
assert.equal(sameKept.counterfactual, "상위 1개 변수를 학습 데이터 중앙값으로 바꾸면 원점수가 판정선과 같아져 여전히 판정선 기준 이상 범위에 남습니다.");
assert.ok(sameKept.summary.includes("(값 0.710, 판정선 0.620)"));
const sameAlone = sentences({ counterfactual: [
  { replaced: ["error_count"], scoreAfter: 0.62, warningAfterReplacement: true },
  { replaced: ["error_count", "elapsed_ms"], scoreAfter: 0.41, warningAfterReplacement: false },
  { replaced: ["error_count", "elapsed_ms", "total_count"], scoreAfter: 0.4, warningAfterReplacement: false },
], clearedAtK: 2 });
assert.equal(sameAlone.counterfactual, "ERROR_COUNT·처리 시간을 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 낮아져(값 0.410) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. "
  + "ERROR_COUNT만 바꾸면 원점수가 판정선과 같아 여전히 판정선 기준 이상 범위에 남습니다.");
const sameCleared = sentences({ counterfactual: base().counterfactual.map((c, i) => ({ ...c, scoreAfter: [0.62, 0.41, 0.4][i] })) });
assert.equal(sameCleared.counterfactual, "ERROR_COUNT(단위 미확인)만 학습 데이터 중앙값(0)으로 바꾸면 원점수가 판정선과 같아져 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.",
  "stored warningAfterReplacement=false keeps the cleared branch; only the value part changes");
// tol 경계: tol = max(1e-9, 1e-9·|score|) — 차가 tol 바로 안은 같음, 바로 밖은 숫자(자릿수 늘리기로 갈림)
assert.ok(sentences({ scoreActual: 0.62 + 9e-10 }).summary.includes("(판정선과 같음, 판정선 0.620)"));
const justOutside = sentences({ scoreActual: 0.62 + 2e-9 });
assert.ok(justOutside.summary.includes("(값 0.620000, 판정선 0.620000)"), justOutside.summary);
assert.ok(readAttribution({ methodId: "ml:iforest", category: "ml", score: 10, attribution: base({ scoreActual: 0.62 + 5e-9 }) }).status === "succeeded");
assert.ok(attributionSentences(readAttribution({ methodId: "ml:iforest", category: "ml", score: 10, attribution: base({ scoreActual: 0.62 + 5e-9 }) })).summary
  .includes("(판정선과 같음, 판정선 0.620)"), "tol from the method score (10 → 1e-8)");
// 같은 점수는 자릿수 늘리기에서 빠지고 나머지 보이는 점수끼리만 늘린다
const restDigits = sentences({ scoreActual: 0.62, counterfactual: keptCf([0.66, 0.65, 0.6203]), clearedAtK: null });
assert.ok(restDigits.summary.startsWith("머신러닝 모델 원점수(판정선과 같음, 판정선 0.6200)"), restDigits.summary);
assert.ok(restDigits.counterfactual.endsWith("(값 0.6203)."));
assert.ok(sentences({ scoreActual: 0.62, counterfactual: keptCf([0.66, 0.65, 0.64]), clearedAtK: null }).summary.includes("판정선 0.620)"), "only same scores → 3 digits");
// 13) 형식 방어: 판정선은 10진 문자열만(0x10·Infinity·공백은 읽을 수 없는 기록), 지수가 상한 밖이면 원문 그대로, 음수 0은 부호 없이
for (const threshold of ["0x10", "Infinity", " 0.62", "0.62 ", "1e", ".5", "NaN"]) assert.equal(read(base({ threshold })).status, "unreadable", threshold);
assert.equal(read(base({ threshold: "6.2E-1" })).status, "succeeded");
assert.equal(read(base({ threshold: "1E+999" })).status, "succeeded", "out-of-limit exponent is kept as text, not rejected");
assert.equal(roundHalfUp("1E+999", 3), "1E+999");
assert.deepEqual([roundHalfUp("-0.0004", 3), roundHalfUp("-0.0005", 3), roundHalfUp("-0", 3)], ["0.000", "-0.001", "0.000"], "negative zero has no sign");
assert.ok(sentences({ direction: "lower_is_anomaly", scoreActual: -0.0001, threshold: "-0.62" }).summary.includes("(값 0.000, 판정선 -0.620)"));
assert.equal(groupDigits("1".repeat(1001)), "1".repeat(1001), "over-long number text is kept as is");
assert.equal(groupDigits("1".repeat(1000)).length, 1000 + 333);
// 이름표는 자기 키만(상속 속성 이름을 집지 않음)
assert.deepEqual(["constructor", "__proto__", "toString"].map(text.featureName), ["constructor", "__proto__", "toString"]);

// 10) evidence v4 읽기와 방법 표: ML 행에만, 결과 대기 행은 없음, 내부 낱말·코드 문자열 없음
const { MethodResults } = await import("../src/features/analysis/components/MethodResults.tsx");
const v4 = readEvidenceV4({ schemaVersion: 4, composite: {}, categoryResults: {}, methodResults: [
  { methodId: "hard:process-status", category: "hard", status: "succeeded", anomalous: false, attribution: base() },
  { methodId: "ml:iforest", category: "ml", status: "succeeded", anomalous: true, score: 0.71, scoreDetail: { raw: 0.71, threshold: "0.62" }, attribution: base() },
  { methodId: "ml:old", category: "ml", status: "succeeded", anomalous: false, score: 0.3 },
  { methodId: "ml:skip", category: "ml", status: "succeeded", anomalous: false, attribution: { version: "baseline-shapley-v1", status: "skipped", reason: "not_anomalous" } },
  { methodId: "ml:fail", category: "ml", status: "succeeded", anomalous: true, attribution: { version: "baseline-shapley-v1", status: "failed", failureCode: "exception", computeMs: 2 } },
  { methodId: "ml:bad", category: "ml", status: "succeeded", anomalous: true, attribution: { version: "baseline-shapley-v1", status: "succeeded" } },
  { methodId: "ml:wait", category: "ml", status: "not_ready", anomalous: null, cause: "pending" },
] });
assert.deepEqual(v4.methodResults.map((m) => m.attribution?.status ?? null), [null, "succeeded", "absent", "skipped", "failed", "unreadable", "absent"]);
const html = renderToStaticMarkup(React.createElement(MethodResults, { observedStatus: null, evidence: v4, final: true }));
assert.deepEqual([...html.matchAll(/data-attribution-of="([^"]+)"/g)].map((m) => m[1]), ["ml:iforest", "ml:old", "ml:skip", "ml:fail", "ml:bad"],
  "final: ML rows with a key, plus succeeded ML rows without one; no not_ready row without a key");
// 중간 판정 기록(final=false): 키 없는 ML 행에는 `기여도 미계산` 줄이 없다. 키가 있으면 그대로
const interimHtml = renderToStaticMarkup(React.createElement(MethodResults, { observedStatus: null, evidence: v4 }));
assert.deepEqual([...interimHtml.matchAll(/data-attribution-of="([^"]+)"/g)].map((m) => m[1]), ["ml:iforest", "ml:skip", "ml:fail", "ml:bad"]);
// 최종 판정이라도 성공이 아닌(기한 초과·실패) ML 행에 키가 없으면 줄이 없다
const notSucceeded = readEvidenceV4({ schemaVersion: 4, composite: {}, categoryResults: {}, methodResults: [
  { methodId: "ml:late", category: "ml", status: "not_ready", anomalous: null, cause: "timeout" },
  { methodId: "ml:broken", category: "ml", status: "failed", anomalous: null },
  { methodId: "ml:late2", category: "ml", status: "not_ready", anomalous: null, cause: "timeout", attribution: { version: "baseline-shapley-v1", status: "skipped", reason: "method_unresolved" } },
] });
assert.deepEqual([...renderToStaticMarkup(React.createElement(MethodResults, { observedStatus: null, evidence: notSucceeded, final: true })).matchAll(/data-attribution-of="([^"]+)"/g)].map((m) => m[1]),
  ["ml:late2"]);
const statusOf = (id) => html.match(new RegExp(`data-attribution-of="${id}".*?data-attribution-status="([a-z]+)"[^>]*>([^<]*)<`))?.slice(1);
assert.deepEqual(statusOf("ml:old"), ["absent", "기여도 미계산"]);
assert.deepEqual(statusOf("ml:skip"), ["skipped", "기여도 계산 안 함 · 이상 판정이 아님"]);
assert.deepEqual(statusOf("ml:fail"), ["failed", "기여도 계산 실패 · 그 밖의 오류"]);
assert.deepEqual(statusOf("ml:bad"), ["unreadable", "기여도 표시 불가"]);
for (const shown of [example.summary, example.counterfactual, LIMIT, "학습 데이터 중앙값", "4,200 ms", "56%"]) assert.ok(html.includes(shown), shown);
for (const hidden of ["평소 값 1", "평소 값 0", "baseline-shapley-v1", "Shapley", "리비전", "not_anomalous", "exception", "too_many_players", "총 건수", "오류율", "재시도 수", "error_count<", ">error_count"])
  assert.ok(!html.includes(hidden), `hidden ${hidden}`);
assert.ok(!/\d건|건 /.test(html.replace(/조건/g, "")), "no 건 unit word");
console.log("ml-attribution: ok");
