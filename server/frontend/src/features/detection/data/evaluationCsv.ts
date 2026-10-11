import type { EvaluationCsvMetadata } from "./evaluationTypes";

export const evaluationCsvLimits = {
  bytes: 5 * 1024 * 1024,
  rows: 10000,
} as const;
type CsvCheck = { metadata: EvaluationCsvMetadata | null; errors: string[] };

// Preparation-only proposed CSV shape. Never retain or transmit raw rows.
export const inspectEvaluationCsv = (text: string): CsvCheck => {
  const fail = (message: string): CsvCheck => ({
    metadata: null,
    errors: [message],
  });
  if (new TextEncoder().encode(text).byteLength > evaluationCsvLimits.bytes)
    return fail("CSV는 5 MiB 이하로 선택해 주세요.");
  text = text.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let row: string[] = [],
    field = "",
    quoted = false,
    afterQuote = false;
  const pushField = () => {
    row.push(field);
    field = "";
    afterQuote = false;
  };
  const pushRow = () => {
    pushField();
    if (row.some((value) => value.trim())) rows.push(row);
    row = [];
  };
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (char === '"') {
        quoted = false;
        afterQuote = true;
      } else field += char;
    } else if (char === '"') {
      if (field || afterQuote) return fail("CSV 따옴표 위치를 확인해 주세요.");
      quoted = true;
    } else if (char === ",") pushField();
    else if (char === "\n" || char === "\r") {
      if (char === "\r" && text[i + 1] === "\n") i++;
      pushRow();
    } else if (afterQuote && !/\s/.test(char))
      return fail("닫힌 따옴표 뒤에는 구분자가 필요합니다.");
    else if (!afterQuote) field += char;
    if (rows.length > evaluationCsvLimits.rows + 1)
      return fail("CSV 데이터 행은 10000개 이하로 선택해 주세요.");
  }
  if (quoted) return fail("CSV 따옴표가 닫히지 않았습니다.");
  if (field || row.length || afterQuote) pushRow();
  if (rows.length < 2) return fail("헤더와 시험 데이터 행이 필요합니다.");
  const headers = rows[0].map((value) =>
    value
      .replace(/^\uFEFF/, "")
      .trim()
      .toLowerCase(),
  );
  if (
    headers.some((value) => !value) ||
    new Set(headers).size !== headers.length
  )
    return fail("CSV 헤더가 비어 있거나 중복되었습니다.");
  const scenarioIndex = headers.indexOf("scenario"),
    labelIndex = headers.indexOf("label");
  if (scenarioIndex < 0 || labelIndex < 0)
    return fail("CSV에는 scenario, label 헤더가 필요합니다.");
  const metadata: EvaluationCsvMetadata = {
    columns: headers.filter(
      (name) => !/body|password|secret|token/i.test(name),
    ),
    rowCount: 0,
    labels: {},
    scenarios: {},
    samples: [],
  };
  for (const values of rows.slice(1)) {
    if (values.length !== headers.length)
      return fail("헤더와 데이터 열 개수가 일치하지 않습니다.");
    const scenario = values[scenarioIndex].trim().toLowerCase(),
      label = values[labelIndex].trim().toLowerCase();
    if (!["delay", "stall", "burst", "normal"].includes(scenario))
      return fail("scenario는 delay/stall/burst/normal 중 하나여야 합니다.");
    if (!["anomaly", "normal", "unknown"].includes(label))
      return fail("label은 anomaly/normal/unknown 중 하나여야 합니다.");
    metadata.rowCount++;
    metadata.labels[label] = (metadata.labels[label] ?? 0) + 1;
    metadata.scenarios[scenario] = (metadata.scenarios[scenario] ?? 0) + 1;
    if (metadata.samples.length < 5) metadata.samples.push({ scenario, label });
  }
  if (metadata.rowCount > evaluationCsvLimits.rows)
    return fail("CSV 데이터 행은 10000개 이하로 선택해 주세요.");
  return { metadata, errors: [] };
};
