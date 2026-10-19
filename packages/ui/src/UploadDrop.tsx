import { useRef, useState, type ReactNode } from "react";
import { cx } from "./cx";

export function UploadDrop({ accept, onFile, children, disabled }: {
  accept: string;
  onFile: (f: File) => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files[0];
        if (f && !disabled) onFile(f);
      }}
      className={cx("rounded-panel border-2 border-dashed transition-colors duration-150",
        over ? "border-accent bg-emerald-50" : "border-border bg-panel")}
    >
      <button type="button" disabled={disabled} onClick={() => input.current?.click()}
        className="flex w-full flex-col items-center justify-center gap-3 p-8 text-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent">
        {children}
      </button>
      <input ref={input} type="file" accept={accept} className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }} />
    </div>
  );
}
