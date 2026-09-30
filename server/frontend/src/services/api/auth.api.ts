import { httpClient } from "./client";
import type {
  AuthSession,
  AuthUser,
  LoginCredentials,
  LoginResponse,
  MeResponse,
} from "../../types/auth";
import { isAuthUser } from "../../types/auth";

const parseLoginResponse = (response: LoginResponse): AuthSession => {
  if (
    typeof response?.token !== "string" ||
    response.token.length === 0 ||
    !isAuthUser(response.user)
  ) {
    throw new Error("로그인 응답 형식이 올바르지 않습니다.");
  }

  return {
    token: response.token,
    user: response.user,
  };
};

const parseMeResponse = (response: MeResponse): AuthUser => {
  if (!isAuthUser(response?.user)) {
    throw new Error("사용자 정보 응답 형식이 올바르지 않습니다.");
  }

  return response.user;
};

export async function login(credentials: LoginCredentials): Promise<AuthSession> {
  const response = await httpClient.post<LoginResponse>(
    "/api/auth/login",
    credentials,
    { includeAuth: false },
  );

  return parseLoginResponse(response);
}

export async function fetchCurrentUser(): Promise<AuthUser> {
  const response = await httpClient.get<MeResponse>("/api/auth/me");
  return parseMeResponse(response);
}
