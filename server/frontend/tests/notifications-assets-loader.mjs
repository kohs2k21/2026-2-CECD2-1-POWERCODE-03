export const load = (url, context, nextLoad) =>
  url.endsWith(".svg")
    ? {
        format: "module",
        source: 'export default "test-logo.svg"',
        shortCircuit: true,
      }
    : nextLoad(url, context);
