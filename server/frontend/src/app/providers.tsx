import { useState, type PropsWithChildren } from "react";
import { ToastBar, Toaster } from "react-hot-toast";
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
        >
          {/*
            등장·퇴장은 움직임 토큰(ui.md "움직임")으로 통일한다. 높이를 재기 전(라이브러리가 투명하게 둠)에는 움직임을 붙이지 않는다.
            동작 줄이기는 styles.css base 미디어쿼리가 길이를 없앤다.
          */}
          {(toast) => (
            <ToastBar
              toast={toast}
              style={toast.height ? { ...toast.style, animation: toast.visible ? "var(--animate-toast-in)" : "var(--animate-toast-out)" } : toast.style}
            />
          )}
        </Toaster>
      </MotionConfig>
    </QueryClientProvider>
  );
};
