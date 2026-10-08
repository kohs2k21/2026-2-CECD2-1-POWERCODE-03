import type { PropsWithChildren } from "react";
import { Toaster } from "react-hot-toast";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { AuthSessionProvider } from "../services/auth/AuthSessionProvider";

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
      <AuthSessionProvider>{children}</AuthSessionProvider>
      <Toaster
        position="bottom-right"
        toastOptions={{
          duration: 2400,
          style: {
            border: "1px solid #ebebeb",
            borderRadius: "12px",
            boxShadow: "0 12px 32px rgb(0 0 0 / 10%)",
            color: "#171717",
            fontSize: "13px",
          },
        }}
      />
    </QueryClientProvider>
  );
};
