import path from "node:path";
import dotenv from "dotenv";
import type { UserType } from "../models/user.js";

dotenv.config();

function requiredSecret(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} is required`);
  }
  return value;
}

function optionalSeedPair(
  emailName: string,
  passwordName: string,
  userType: UserType,
): SeedAccount | null {
  const email = process.env[emailName]?.trim();
  const password = process.env[passwordName];

  if (!email && !password) {
    return null;
  }

  if (!email || !password?.trim()) {
    throw new Error(`${emailName} and ${passwordName} must be provided together`);
  }

  return { email, password, userType };
}

export interface SeedAccount {
  email: string;
  password: string;
  userType: UserType;
}

export const JWT_SECRET = requiredSecret("JWT_SECRET");
export const JWT_EXPIRES_IN = process.env.JWT_EXPIRES_IN?.trim() || "24h";

const parsedPort = Number.parseInt(process.env.PORT || "5001", 10);
if (!Number.isInteger(parsedPort) || parsedPort < 0 || parsedPort > 65535) {
  throw new Error("PORT must be an integer between 0 and 65535");
}
export const PORT = parsedPort;

const configuredDataPath = process.env.USER_DATA_PATH?.trim();
export const USER_DATA_PATH = path.resolve(
  configuredDataPath || path.join(process.cwd(), "data", "users.json"),
);

const seedAccounts = [
  optionalSeedPair("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "admin"),
  optionalSeedPair("SEED_USER_EMAIL", "SEED_USER_PASSWORD", "user"),
].filter((account): account is SeedAccount => account !== null);

const seedEmails = new Set<string>();
for (const account of seedAccounts) {
  const normalizedEmail = account.email.toLowerCase();
  if (seedEmails.has(normalizedEmail)) {
    throw new Error("Seed account email values must be unique");
  }
  seedEmails.add(normalizedEmail);
}

export const SEEDED_ACCOUNTS: readonly SeedAccount[] = seedAccounts;
