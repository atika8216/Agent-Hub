import {
  type FormEvent,
  type KeyboardEvent,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Send, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

interface ChatInputProps {
  onSend: (message: string) => void;
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
  placeholder?: string;
  variant?: "large" | "normal";
}

/*
 * iOS iMessage composer:
 *   - large: used on the empty-state landing card; generous padding.
 *   - normal: footer composer inside an active chat; tight vertical.
 *
 * The form itself is a fully-rounded pill on iOS -- ``--radius-xl``
 * (18px) matches the outer message bubble radius, so the composer
 * visually belongs to the same conversation stream.
 */
const SIZING = {
  large: {
    form: "p-2 rounded-[var(--radius-xl)]",
    textarea: "min-h-[44px] max-h-[140px] px-3 py-2 text-[0.9375rem]",
    button: "h-10 w-10 rounded-full",
    icon: "h-[18px] w-[18px]",
  },
  normal: {
    form: "p-[6px] rounded-[var(--radius-xl)]",
    textarea: "min-h-[38px] max-h-[120px] px-3 py-1.5 text-[0.9375rem]",
    button: "h-8 w-8 rounded-full",
    icon: "h-[14px] w-[14px]",
  },
} as const;

export const ChatInput = memo(function ChatInput({
  onSend,
  onStop,
  isStreaming = false,
  disabled = false,
  placeholder = "Type a message...",
  variant = "normal",
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sizes = SIZING[variant];

  const resize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const max = variant === "large" ? 140 : 120;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, [variant]);

  useEffect(() => {
    resize();
  }, [value, resize]);

  const submit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled || isStreaming) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, disabled, isStreaming, onSend]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
    },
    [submit],
  );

  const handleSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      submit();
    },
    [submit],
  );

  return (
    <form
      onSubmit={handleSubmit}
      className={[
        "flex w-full min-w-0 items-end gap-2",
        "border border-border bg-surface-elevated",
        "shadow-[0_1px_2px_0_oklch(0_0_0/0.04)]",
        "dark:shadow-[inset_0_1px_0_oklch(1_0_0/0.04)]",
        "transition-[border-color,box-shadow] duration-150",
        "focus-within:border-info focus-within:ring-2 focus-within:ring-info/20",
        sizes.form,
      ].join(" ")}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled && !isStreaming}
        rows={1}
        className={`flex-1 min-w-0 w-full resize-none bg-transparent text-text-primary placeholder:text-text-muted outline-none disabled:opacity-50 ${sizes.textarea}`}
      />

      {isStreaming && onStop ? (
        <Button
          type="button"
          size="icon"
          variant="secondary"
          onClick={onStop}
          className={`shrink-0 ${sizes.button}`}
          title="Stop generation"
        >
          <Square className={`${sizes.icon} fill-current`} />
        </Button>
      ) : (
        <Button
          type="submit"
          size="icon"
          disabled={disabled || !value.trim()}
          className={`shrink-0 ${sizes.button}`}
          title="Send message (Enter)"
        >
          <Send className={sizes.icon} />
        </Button>
      )}
    </form>
  );
});
