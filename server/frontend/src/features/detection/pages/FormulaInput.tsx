import { useId, useRef, useState } from "react";
import { Button } from "../../../components/ui/button";
import { formulaFunctions, type FormulaFeature } from "../data/formula";

type Suggestion = { id: string; label: string; insert: string };
export const FormulaInput = ({
  value,
  features,
  onChange,
}: {
  value: string;
  features: FormulaFeature[];
  onChange: (value: string) => void;
}) => {
  const input = useRef<HTMLTextAreaElement>(null);
  const listId = useId();
  const [search, setSearch] = useState("");
  const [caret, setCaret] = useState(value.length);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const refs: Suggestion[] = features.map((feature) => ({
    id: feature.id,
    label:
      feature.source === "derived"
        ? feature.name
        : `${feature.source?.toUpperCase()}.${feature.name}`,
    insert: `[${feature.id}]`,
  }));
  const functions: Suggestion[] = formulaFunctions.map((fn) => ({
    id: fn.name,
    label: `${fn.name} · ${fn.description}`,
    insert: `${fn.name}()`,
  }));
  const match = /(?:\[)?[\w.\-]+$/.exec(value.slice(0, caret));
  const term = (match?.[0] ?? "").replace(/^\[/, "").toLowerCase();
  const suggestions = term
    ? [...refs, ...functions]
        .filter((item) =>
          `${item.id} ${item.label}`.toLowerCase().includes(term),
        )
        .sort(
          (a, b) =>
            Number(!a.id.toLowerCase().startsWith(term)) -
            Number(!b.id.toLowerCase().startsWith(term)),
        )
        .slice(0, 8)
    : [];
  const insert = (text: string, autocomplete = false) => {
    const element = input.current;
    const start =
      autocomplete && match
        ? caret - match[0].length
        : (element?.selectionStart ?? caret);
    let end = autocomplete ? caret : (element?.selectionEnd ?? caret);
    if (autocomplete) {
      const suffix = /^(?:[\w.\-]*)(?:\])?/.exec(value.slice(caret))?.[0] ?? "";
      end += suffix.length;
      if (text.endsWith("()") && value[end] === "(") text = text.slice(0, -2);
    }
    const next = value.slice(0, start) + text + value.slice(end);
    onChange(next);
    const position = start + text.length - (text.endsWith("()") ? 1 : 0);
    setCaret(position);
    setOpen(false);
    requestAnimationFrame(() => {
      element?.focus();
      element?.setSelectionRange(position, position);
    });
  };
  return (
    <div className="formula-input">
      <label className="detection-field">
        수식
        <textarea
          ref={input}
          value={value}
          maxLength={2048}
          rows={4}
          spellCheck={false}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open && suggestions.length > 0}
          aria-controls={listId}
          aria-activedescendant={
            open && suggestions[active] ? `${listId}-${active}` : undefined
          }
          onChange={(event) => {
            onChange(event.target.value);
            setCaret(event.target.selectionStart);
            setActive(0);
            setOpen(true);
          }}
          onClick={(event) => {
            setCaret(event.currentTarget.selectionStart);
            setActive(0);
            setOpen(true);
          }}
          onSelect={(event) => {
            setCaret(event.currentTarget.selectionStart);
            setActive(0);
          }}
          onBlur={() => setOpen(false)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setOpen(false);
              event.stopPropagation();
              return;
            }
            if (
              open &&
              suggestions.length &&
              ["ArrowDown", "ArrowUp", "Enter"].includes(event.key)
            ) {
              event.preventDefault();
              if (event.key === "Enter")
                insert(
                  suggestions[active]?.insert ?? suggestions[0].insert,
                  true,
                );
              else
                setActive(
                  (index) =>
                    (index +
                      (event.key === "ArrowDown" ? 1 : -1) +
                      suggestions.length) %
                    suggestions.length,
                );
            }
          }}
        />
      </label>
      {open && suggestions.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          aria-label="수식 자동완성"
          className="formula-suggestions"
        >
          {suggestions.map((item, index) => (
            <li
              key={item.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === active}
              onMouseDown={(event) => {
                event.preventDefault();
                insert(item.insert, true);
              }}
            >
              {item.label}
            </li>
          ))}
        </ul>
      )}
      <p className="detection-note">
        속성 이름을 검색해 삽입하거나 수식을 직접 입력하세요. 예:
        [process.ERROR_COUNT] / [process.TOTAL_COUNT]
      </p>
      <details className="formula-reference">
        <summary>속성·함수 찾아 넣기</summary>
        <label className="detection-field">
          수식 입력 속성 검색
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="속성명·Transaction·Process·함수명"
          />
        </label>
        <div
          className="formula-reference-list"
          aria-label="사용 가능한 속성·함수"
        >
          {[...refs, ...functions]
            .filter((item) =>
              `${item.id} ${item.label}`
                .toLowerCase()
                .includes(search.toLowerCase()),
            )
            .map((item) => (
              <Button
                key={item.id}
                variant="ghost"
                size="sm"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => insert(item.insert)}
              >
                {item.label}
              </Button>
            ))}
        </div>
      </details>
    </div>
  );
};
