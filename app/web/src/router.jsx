import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { useEffect, useRef } from "react";
import { Toaster, toast } from "sonner";
import App from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";

// One screen: the chat workspace. The active thread lives in `?chat=<id>`.
const router = createBrowserRouter([
  {
    path: "/",
    element: (
      <AppErrorBoundary>
        <App view="chat" />
      </AppErrorBoundary>
    ),
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);

function describeError(errorLike) {
  if (!errorLike) return "Unexpected application error";
  if (typeof errorLike === "string") return errorLike;
  if (typeof errorLike.reason === "string") return errorLike.reason;
  return errorLike.message || errorLike.reason?.message || "Unexpected application error";
}

function GlobalErrorToasts() {
  const lastMessageRef = useRef("");

  useEffect(() => {
    function emitToast(message, title = "Unexpected error") {
      if (!message) return;
      if (lastMessageRef.current === message) return;
      lastMessageRef.current = message;
      toast.error(title, { description: message });
      window.setTimeout(() => {
        if (lastMessageRef.current === message) lastMessageRef.current = "";
      }, 1500);
    }

    function onWindowError(event) {
      emitToast(describeError(event.error || event.message), "Application error");
    }

    function onUnhandledRejection(event) {
      emitToast(describeError(event.reason), "Unhandled error");
    }

    window.addEventListener("error", onWindowError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);

    return () => {
      window.removeEventListener("error", onWindowError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  return null;
}

export function AppRouter() {
  return (
    <>
      <GlobalErrorToasts />
      <RouterProvider router={router} />
      <Toaster position="top-right" richColors />
    </>
  );
}
