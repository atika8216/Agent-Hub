import { useEffect, useRef, useState } from "react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  debounceMs?: number;
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search agents\u2026",
  debounceMs = 250,
}: SearchInputProps) {
  const [local, setLocal] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  function handleChange(next: string) {
    setLocal(next);
    if (timerRef.current != null) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onChange(next), debounceMs);
  }

  return (
    <div className="relative">
      <svg
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="11" cy="11" r="8" />
        <path d="m21 21-4.3-4.3" />
      </svg>
      <input
        type="search"
        value={local}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={placeholder}
        className={[
          "block h-9 w-full rounded-[var(--radius-md)]",
          "border border-border bg-surface-elevated",
          "pl-9 pr-8 text-[0.875rem] text-text-primary placeholder:text-text-muted",
          "transition-[border-color,box-shadow] duration-150",
          "focus:border-info focus:outline-none focus:ring-2 focus:ring-info/30",
          "[&::-webkit-search-cancel-button]:hidden",
        ].join(" ")}
      />
      {local && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => handleChange("")}
          className={[
            "absolute right-2 top-1/2 -translate-y-1/2",
            "flex h-5 w-5 items-center justify-center rounded-full",
            "bg-surface-overlay text-text-muted",
            "transition-colors hover:text-text-primary",
          ].join(" ")}
        >
          <svg
            className="h-3 w-3"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  );
}
