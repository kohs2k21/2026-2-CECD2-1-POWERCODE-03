import type { PropsWithChildren } from "react";
import { Toaster } from "react-hot-toast";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { AuthSessionProvider } from "../services/auth/AuthSessionProvider";
import { MotionConfig } from "motion/react";

export const AppProvider = ({ children }: PropsWithChildren) => {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false },
        },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <AuthSessionProvider>{children}</AuthSessionProvider>
        <Toaster
          position="bottom-right"
          toastOptions={{
            className: "ui-toast",
            style: {
              border: "1px solid var(--hairline)",
              borderRadius: "var(--radius-lg)",
              boxShadow: "var(--shadow-toast)",
              color: "var(--ink)",
              background: "var(--canvas)",
              fontSize: "13px",
            },
          }}
          containerStyle={{ zIndex: "var(--z-toast)" }}
        />
      </MotionConfig>
    </QueryClientProvider>
  );
};
