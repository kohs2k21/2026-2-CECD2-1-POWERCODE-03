import { useState, type FormEvent } from "react";
import { login } from "../../services/api/auth.api";
import type { AuthSession } from "../../types/auth";
import "./auth.css";

type AuthPageProps = {
  onLoginSuccess: (session: AuthSession) => void;
  authError?: string | null;
  onRetryAuth?: () => void;
};

export const AuthPage = ({
  onLoginSuccess,
  authError = null,
  onRetryAuth,
}: AuthPageProps) => {
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
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "로그인에 실패했습니다. 잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-card__eyebrow">ESB Anomaly Detection</div>
        <h1 id="auth-title">이상 징후 탐지 대시보드</h1>
        <p className="auth-card__description">
          사전 등록된 계정으로 로그인해 관제 화면을 이용하세요.
        </p>

        <form className="auth-form" onSubmit={handleSubmit}>
          {authError ? (
            <div className="auth-form__session-error">
              <p role="alert">{authError}</p>
              {onRetryAuth ? (
                <button type="button" onClick={onRetryAuth}>
                  로그인 상태 다시 확인
                </button>
              ) : null}
              <span>아래에서 다른 계정으로 로그인할 수 있습니다.</span>
            </div>
          ) : null}
          <label className="auth-form__field">
            <span>이메일</span>
            <input
              type="email"
              name="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="username"
              required
              disabled={isSubmitting}
            />
          </label>

          <label className="auth-form__field">
            <span>비밀번호</span>
            <input
              type="password"
              name="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
              disabled={isSubmitting}
            />
          </label>

          {errorMessage && (
            <p className="auth-form__error" role="alert">
              {errorMessage}
            </p>
          )}

          <button type="submit" disabled={isSubmitting}>
            {isSubmitting
              ? "로그인 중..."
              : authError
                ? "다른 계정으로 로그인"
                : "로그인"}
          </button>
        </form>
      </section>
    </main>
  );
};
