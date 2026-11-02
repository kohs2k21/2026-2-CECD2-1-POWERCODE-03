import { useState, type PropsWithChildren } from "react";
import { Toaster } from "react-hot-toast";
import { QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { createAppQueryClient } from "@/lib/query";
import { AuthSessionProvider } from "@/services/auth/AuthSessionProvider";

/** 앱 공통 공급자: Query(공통 조회 규칙) · Motion(reduced-motion) · 세션 · Toaster 1개(ui.md). */
export const AppProvider = ({ children }: PropsWithChildren) => {
  const [queryClient] = useState(createAppQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <AuthSessionProvider>{children}</AuthSessionProvider>
        <Toaster
          position="bottom-right"
          containerStyle={{ zIndex: "var(--z-toast)" }}
          toastOptions={{
            className: "rounded-lg! border! border-border! bg-card! text-sm! text-card-foreground! shadow-toast!",
          }}
        />
      </MotionConfig>
    </QueryClientProvider>
  );
};
