export type UserType = "user" | "admin";

export interface User {
  id: string;
  email: string;
  password: string;
  userType: UserType;
  createdAt: string;
}

export interface JWTPayload {
  id: string;
  email: string;
  userType: UserType;
}
