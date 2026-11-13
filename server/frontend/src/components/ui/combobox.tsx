import { IconCheck, IconSearch, IconSelector } from "@tabler/icons-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type ComboboxOption = { value: string; label: string; detail?: string };

/** 검색어 부분 일치(대소문자 무시, 앞뒤 공백 제거). 빈 검색어는 전체. 순서는 받은 순서 그대로. */
export const filterComboboxOptions = (options: readonly ComboboxOption[], query: string) => {
  const q = query.trim().toLowerCase();
  return q ? options.filter((option) => option.value.toLowerCase().includes(q) || option.label.toLowerCase().includes(q)) : [...options];
};

type ComboboxProps = {
  value: string | null;
  onValueChange: (value: string) => void;
  options: readonly ComboboxOption[];
  /** 트리거에 보일 선택값 문구(없으면 선택 항목 label). */
  valueText?: string;
  searchLabel: string;
  emptyText: string;
  "aria-labelledby"?: string;
  size?: "sm" | "default";
  className?: string;
};

/**
 * 항목이 많은 선택(수백 개)용 검색 선택. 트리거 → 팝오버(검색 입력 + 목록). 입력에서 ↑↓·Home·End로 이동, Enter로 선택, Esc로 닫기.
 * 접근성: 입력 role=combobox + aria-activedescendant, 목록 role=listbox/option(선택 항목 aria-selected).
 */
export const Combobox = ({ value, onValueChange, options, valueText, searchLabel, emptyText, size = "default", className, ...aria }: ComboboxProps) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => filterComboboxOptions(options, query), [options, query]);
  const selected = options.find((option) => option.value === value);
  const optionId = (index: number) => `${listId}-${index}`;

  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [active, open]);

  const onOpenChange = (next: boolean) => {
    setOpen(next);
    setQuery("");
    setActive(Math.max(0, options.findIndex((option) => option.value === value)));
  };
  const choose = (next: string) => {
    onOpenChange(false);
    if (next !== value) onValueChange(next);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = shown.length - 1;
    const move = { ArrowDown: Math.min(active + 1, last), ArrowUp: Math.max(active - 1, 0), Home: 0, End: last }[event.key];
    if (move !== undefined) {
      event.preventDefault();
      setActive(Math.max(0, move));
    } else if (event.key === "Enter" && shown[active]) {
      event.preventDefault();
      choose(shown[active].value);
    }
  };

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-slot="combobox-trigger"
          data-size={size}
          aria-labelledby={aria["aria-labelledby"]}
          className={cn(
            "flex w-fit items-center justify-between gap-2 whitespace-nowrap rounded-md border border-input bg-card px-3 py-2 text-sm text-foreground shadow-xs transition-[color,box-shadow] outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 data-[size=default]:h-9 data-[size=sm]:h-8",
            className,
          )}
        >
          <span className="truncate">{valueText ?? selected?.label ?? ""}</span>
          <IconSelector className="size-4 shrink-0 opacity-50" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-(--radix-popover-trigger-width) min-w-72 p-0">
        <div className="flex items-center gap-2 border-b px-3">
          <IconSearch className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input
            role="combobox"
            aria-label={searchLabel}
            aria-expanded="true"
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={shown[active] ? optionId(active) : undefined}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={searchLabel}
            className="h-10 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <div ref={listRef} id={listId} role="listbox" aria-label={searchLabel} className="max-h-72 overflow-y-auto p-1">
          {shown.length ? (
            shown.map((option, index) => (
              <div
                key={option.value}
                id={optionId(index)}
                role="option"
                data-index={index}
                aria-selected={option.value === value}
                data-active={index === active || undefined}
                onMouseMove={() => index !== active && setActive(index)}
                onClick={() => choose(option.value)}
                className="flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm select-none data-[active]:bg-accent data-[active]:text-accent-foreground"
              >
                <IconCheck className={cn("size-4 shrink-0", option.value === value ? "opacity-100" : "opacity-0")} aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{option.label}</span>
                {option.detail && <span className="shrink-0 text-xs text-muted-foreground">{option.detail}</span>}
              </div>
            ))
          ) : (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">{emptyText}</p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
};
