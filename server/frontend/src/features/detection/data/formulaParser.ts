import {
  FORMULA_LIMITS,
  type FormulaAst,
  type FormulaValue,
} from "./formulaTypes";

type Token = {
  kind: "number" | "string" | "id" | "ref" | "symbol" | "eof";
  text: string;
};
const tokenize = (source: string): Token[] => {
  if (!source.trim()) throw new Error("수식을 입력해 주세요.");
  if (source.length > FORMULA_LIMITS.length)
    throw new Error("수식은 2048자 이하여야 합니다.");
  const tokens: Token[] = [];
  let offset = 0;
  const push = (kind: Token["kind"], text: string) => {
    tokens.push({ kind, text });
    if (tokens.length > FORMULA_LIMITS.tokens)
      throw new Error("수식 항목은 512개 이하여야 합니다.");
  };
  while (offset < source.length) {
    const rest = source.slice(offset);
    if (/^\s/.test(rest)) {
      offset++;
      continue;
    }
    if (rest[0] === "[") {
      const end = rest.indexOf("]");
      if (end < 0 || !rest.slice(1, end).trim())
        throw new Error("속성 참조의 대괄호를 확인해 주세요.");
      push("ref", rest.slice(1, end).trim());
      offset += end + 1;
      continue;
    }
    if (rest[0] === '"' || rest[0] === "'") {
      const quote = rest[0];
      let value = "";
      let pos = 1;
      while (pos < rest.length && rest[pos] !== quote) {
        if (rest[pos] === "\\") {
          pos++;
          const escaped = rest[pos];
          if (!["\\", '"', "'", "n", "t"].includes(escaped ?? ""))
            throw new Error("문자열 이스케이프를 확인해 주세요.");
          value += escaped === "n" ? "\n" : escaped === "t" ? "\t" : escaped;
        } else value += rest[pos];
        pos++;
      }
      if (rest[pos] !== quote) throw new Error("문자열 따옴표를 닫아 주세요.");
      push("string", value);
      offset += pos + 1;
      continue;
    }
    const numeric = /^(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?/.exec(rest);
    if (numeric) {
      push("number", numeric[0]);
      offset += numeric[0].length;
      continue;
    }
    const id = /^[A-Za-z_][A-Za-z0-9_.]*/.exec(rest);
    if (id) {
      push("id", id[0]);
      offset += id[0].length;
      continue;
    }
    const symbol = /^(?:==|!=|<=|>=|[+\-*/<>() ,])/.exec(rest);
    if (symbol) {
      push("symbol", symbol[0]);
      offset += symbol[0].length;
      continue;
    }
    throw new Error(`허용되지 않는 수식 문자: ${rest[0]}`);
  }
  tokens.push({ kind: "eof", text: "" });
  return tokens;
};
const precedence: Record<string, number> = {
  "==": 1,
  "!=": 1,
  "<": 1,
  "<=": 1,
  ">": 1,
  ">=": 1,
  "+": 2,
  "-": 2,
  "*": 3,
  "/": 3,
};
export const parseFormula = (expression: string): FormulaAst => {
  const tokens = tokenize(expression);
  let cursor = 0;
  const peek = () => tokens[cursor];
  const consume = (text: string) => {
    if (peek().text !== text) throw new Error(`수식에 '${text}'가 필요합니다.`);
    cursor++;
  };
  const parse = (minimum: number, depth: number): FormulaAst => {
    if (depth > FORMULA_LIMITS.depth)
      throw new Error("수식 중첩은 32단계 이하여야 합니다.");
    const token = tokens[cursor++];
    let node: FormulaAst;
    if (token.text === "+" || token.text === "-")
      node = {
        kind: "unary",
        operator: token.text,
        operand: parse(4, depth + 1),
      };
    else if (token.text === "(") {
      node = parse(0, depth + 1);
      consume(")");
    } else if (token.kind === "number") {
      const value = Number(token.text);
      if (!Number.isFinite(value))
        throw new Error("유한한 수치만 입력할 수 있습니다.");
      node = { kind: "literal", value };
    } else if (token.kind === "string")
      node = { kind: "literal", value: token.text };
    else if (token.kind === "ref") node = { kind: "reference", id: token.text };
    else if (token.kind === "id") {
      if (peek().text === "(") {
        cursor++;
        const args: FormulaAst[] = [];
        if (peek().text !== ")") {
          do {
            args.push(parse(0, depth + 1));
            if (peek().text !== ",") break;
            cursor++;
          } while (true);
        }
        consume(")");
        node = { kind: "call", name: token.text.toLowerCase(), args };
      } else if (["true", "false", "null"].includes(token.text.toLowerCase())) {
        const value: FormulaValue =
          token.text.toLowerCase() === "null"
            ? null
            : token.text.toLowerCase() === "true";
        node = { kind: "literal", value };
      } else node = { kind: "reference", id: token.text };
    } else throw new Error("속성·숫자·함수 또는 괄호를 입력해 주세요.");
    while (
      peek().kind === "symbol" &&
      (precedence[peek().text] ?? -1) >= minimum
    ) {
      const operator = tokens[cursor++].text;
      node = {
        kind: "binary",
        operator,
        left: node,
        right: parse(precedence[operator] + 1, depth + 1),
      };
    }
    return node;
  };
  const ast = parse(0, 0);
  if (peek().kind !== "eof")
    throw new Error("수식의 연산자 또는 괄호를 확인해 주세요.");
  return ast;
};
