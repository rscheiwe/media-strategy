import { Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";

export function DeleteChatDialog({ chat, deleting, onCancel, onConfirm }) {
  if (!chat) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 px-4 backdrop-blur-[2px]">
      <div className="w-full max-w-md rounded-lg border border-border bg-background p-5 shadow-lg">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-foreground">Delete chat</div>
            <div className="mt-2 text-sm leading-6 text-muted-foreground">
              This will remove{" "}
              <span className="font-medium text-foreground">{chat.title || "New chat"}</span> and
              its message history.
            </div>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onCancel}
            disabled={deleting}
            aria-label="Close delete confirmation"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="mt-6 flex items-center justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={deleting}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={deleting}
          >
            <Trash2 className="mr-2 h-4 w-4" />
            {deleting ? "Deleting..." : "Delete"}
          </Button>
        </div>
      </div>
    </div>
  );
}
