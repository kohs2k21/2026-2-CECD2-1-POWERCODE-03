import type { UserRole } from "./app";

export type AuthUser = {
  id: string;
  email: string;
  userType: UserRole;
  createdAt: string;
};

export type AuthSession = {
  token: string;
  user: AuthUser;
};

export type LoginCredentials = {
  email: string;
  password: string;
};

export type LoginResponse = {
  message: string;
  token: string;
  user: AuthUser;
};

export type MeResponse = {
  user: AuthUser;
};

export const isAuthUser = (value: unknown): value is AuthUser => {
  if (!value || typeof value !== "object") {
    return false;
  }

  const user = value as Partial<AuthUser>;
  return (
    typeof user.id === "string" &&
    typeof user.email === "string" &&
    (user.userType === "user" || user.userType === "admin") &&
    typeof user.createdAt === "string"
  );
};
