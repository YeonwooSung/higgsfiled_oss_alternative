import * as Slider from "@radix-ui/react-slider";
import { useId } from "react";

export function SliderField({ label, value, min, max, step, onChange, format, disabled }: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[13px]">
        <label htmlFor={id} className="text-ink">{label}</label>
        <input
          id={id}
          type="number"
          className="w-20 rounded-[6px] border border-border bg-panel px-2 py-0.5 text-right tabular-nums focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          value={Number(value.toFixed(4))}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v)));
          }}
          aria-describedby={format ? `${id}-fmt` : undefined}
        />
      </div>
      <Slider.Root
        className="relative flex h-5 w-full touch-none select-none items-center"
        value={[value]}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={([v]) => onChange(v)}
        aria-label={label}
      >
        <Slider.Track className="relative h-1 grow rounded-full bg-border">
          <Slider.Range className="absolute h-full rounded-full bg-accent" />
        </Slider.Track>
        <Slider.Thumb className="block h-4 w-4 rounded-full border border-accent bg-panel shadow focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent" />
      </Slider.Root>
      {format && <p id={`${id}-fmt`} className="text-xs text-muted tabular-nums">{format(value)}</p>}
    </div>
  );
}
