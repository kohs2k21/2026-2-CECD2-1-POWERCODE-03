import { useState, type FormEvent } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { login } from "@/services/api/auth.api";
import type { AuthSession } from "@/types/auth";

type AuthPageProps = {
  onLoginSuccess: (session: AuthSession) => void;
  authError?: string | null;
  onRetryAuth?: () => void;
};

export const AuthPage = ({ onLoginSuccess, authError = null, onRetryAuth }: AuthPageProps) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      const session = await login({ email: email.trim(), password });
      onLoginSuccess(session);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "로그인에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md" aria-labelledby="auth-title">
        <CardHeader>
          <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">ESB Anomaly Detection</p>
          <h1 id="auth-title" className="text-page-title">
            이상 징후 탐지 대시보드
          </h1>
          <CardDescription>사전 등록된 계정으로 로그인해 관제 화면을 이용하세요.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={handleSubmit}>
            {authError && (
              <Alert variant="destructive">
                <AlertDescription>
                  <p>{authError}</p>
                  {onRetryAuth && (
                    <Button variant="outline" size="sm" onClick={onRetryAuth}>
                      로그인 상태 다시 확인
                    </Button>
                  )}
                  <span className="text-muted-foreground">아래에서 다른 계정으로 로그인할 수 있습니다.</span>
                </AlertDescription>
              </Alert>
            )}
            <div className="grid gap-2">
              <Label htmlFor="login-email">이메일</Label>
              <Input
                id="login-email"
                type="email"
                name="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="username"
                required
                disabled={isSubmitting}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="login-password">비밀번호</Label>
              <Input
                id="login-password"
                type="password"
                name="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
                disabled={isSubmitting}
              />
            </div>
            {errorMessage && (
              <p className="text-sm text-destructive-text" role="alert">
                {errorMessage}
              </p>
            )}
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? "로그인 중..." : authError ? "다른 계정으로 로그인" : "로그인"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
};
