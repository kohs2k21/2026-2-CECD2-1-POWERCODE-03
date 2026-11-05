// 평가 결과 데이터 접근의 개발 모드 판정(`import.meta.env?.DEV`)을 시험이 켜고 끌 수 있게 바꾼다. 다른 모듈은 건드리지 않는다.
export const load = async (url, context, nextLoad) => {
  const result = await nextLoad(url, context);
  if (!url.includes("/evaluations/evaluationsApi.ts")) return result;
  const source = String(result.source);
  if (!source.includes("import.meta.env?.DEV")) throw new Error("evaluationsApi no longer reads import.meta.env?.DEV");
  return { ...result, source: source.replace("import.meta.env?.DEV", "globalThis.__EVALUATIONS_DEV__") };
};
