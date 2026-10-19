import { cx } from "./cx";

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string; disabled?: boolean }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-button border border-border bg-app p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          disabled={o.disabled}
          onClick={() => onChange(o.value)}
          className={cx(
            "min-h-[44px] flex-1 rounded-[6px] px-2 text-[13px] transition-colors duration-150 md:min-h-8",
            "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
            o.value === value ? "bg-panel font-medium text-ink shadow-sm" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
