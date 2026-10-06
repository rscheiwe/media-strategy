import { useEffect, useRef, useState } from "react";
import { Pencil, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function RenameChatDialog({ chat, saving, onCancel, onConfirm }) {
  const [title, setTitle] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (!chat) return;
    setTitle(chat.title || "");
    // Focus after the dialog paints so the caret lands in the field.
    requestAnimationFrame(() => inputRef.current?.select());
  }, [chat]);

  if (!chat) return null;
  const trimmed = title.trim();
  const unchanged = trimmed === (chat.title || "").trim();

  function submit(event) {
    event.preventDefault();
    if (!trimmed || unchanged || saving) return;
    onConfirm(trimmed);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4 backdrop-blur-[2px]"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !saving) onCancel();
      }}
    >
      <form
        className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg"
        onSubmit={submit}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="text-sm font-semibold text-foreground">Rename chat</div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onCancel}
            disabled={saving}
            aria-label="Close rename dialog"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <Input
          ref={inputRef}
          className="mt-4"
          value={title}
          maxLength={200}
          onChange={(event) => setTitle(event.target.value)}
          disabled={saving}
          aria-label="Chat title"
        />
        <div className="mt-6 flex items-center justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={!trimmed || unchanged || saving}>
            <Pencil className="mr-2 h-4 w-4" />
            {saving ? "Saving..." : "Rename"}
          </Button>
        </div>
      </form>
    </div>
  );
}
