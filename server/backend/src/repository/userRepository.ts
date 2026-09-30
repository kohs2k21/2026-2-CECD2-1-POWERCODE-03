import fs from "node:fs";
import path from "node:path";
import bcrypt from "bcryptjs";
import { SEEDED_ACCOUNTS, USER_DATA_PATH } from "../config/jwt.js";
import { User } from "../models/user.js";

const DATA_DIR = path.dirname(USER_DATA_PATH);

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function nextUserId(users: User[]): string {
  const numericIds = users
    .map((user) => Number.parseInt(user.id, 10))
    .filter((id) => Number.isInteger(id));
  const nextId = numericIds.length > 0 ? Math.max(...numericIds) + 1 : 1;
  return String(nextId);
}

export class DuplicateEmailError extends Error {
  constructor() {
    super("Email already in use");
    this.name = "DuplicateEmailError";
  }
}

export class UserRepository {
  private static instance: UserRepository;

  private constructor() {
    this.initDatabase();
  }

  public static getInstance(): UserRepository {
    if (!UserRepository.instance) {
      UserRepository.instance = new UserRepository();
    }
    return UserRepository.instance;
  }

  private initDatabase(): void {
    fs.mkdirSync(DATA_DIR, { recursive: true });

    let users: User[];
    if (fs.existsSync(USER_DATA_PATH)) {
      users = this.readUsersFromDisk();
    } else {
      users = [];
      this.writeUsersToDisk(users);
    }

    let changed = false;
    for (const seed of SEEDED_ACCOUNTS) {
      const existing = users.find(
        (user) => normalizeEmail(user.email) === normalizeEmail(seed.email),
      );

      if (!existing) {
        users.push({
          id: nextUserId(users),
          email: normalizeEmail(seed.email),
          password: bcrypt.hashSync(seed.password, 10),
          userType: seed.userType,
          createdAt: new Date().toISOString(),
        });
        changed = true;
      }
    }

    if (changed) {
      this.writeUsersToDisk(users);
    }
  }

  private readUsersFromDisk(): User[] {
    const fileData = fs.readFileSync(USER_DATA_PATH, "utf-8").trim();
    if (!fileData) {
      return [];
    }

    const parsed: unknown = JSON.parse(fileData);
    if (!Array.isArray(parsed)) {
      throw new Error("User data must be a JSON array");
    }

    return parsed as User[];
  }

  private readUsers(): User[] {
    return this.readUsersFromDisk();
  }

  private writeUsersToDisk(users: User[]): void {
    const temporaryPath = `${USER_DATA_PATH}.tmp`;
    fs.writeFileSync(temporaryPath, JSON.stringify(users, null, 2), "utf-8");
    fs.renameSync(temporaryPath, USER_DATA_PATH);
  }

  public async getAll(): Promise<User[]> {
    return this.readUsers();
  }

  public async getById(id: string): Promise<User | null> {
    const users = this.readUsers();
    return users.find((user) => user.id === id) || null;
  }

  public getByIdSync(id: string): User | null {
    const users = this.readUsers();
    return users.find((user) => user.id === id) || null;
  }

  public async getByEmail(email: string): Promise<User | null> {
    const normalizedEmail = normalizeEmail(email);
    const users = this.readUsers();
    return (
      users.find((user) => normalizeEmail(user.email) === normalizedEmail) || null
    );
  }

  public async create(
    userData: Omit<User, "id" | "createdAt">,
  ): Promise<User> {
    const users = this.readUsers();
    const email = normalizeEmail(userData.email);
    if (users.some((user) => normalizeEmail(user.email) === email)) {
      throw new DuplicateEmailError();
    }

    const newUser: User = {
      id: nextUserId(users),
      ...userData,
      email,
      createdAt: new Date().toISOString(),
    };

    users.push(newUser);
    this.writeUsersToDisk(users);
    return newUser;
  }

  public async update(
    id: string,
    updates: Partial<Omit<User, "id" | "createdAt">>,
  ): Promise<User | null> {
    const users = this.readUsers();
    const userIndex = users.findIndex((user) => user.id === id);

    if (userIndex === -1) {
      return null;
    }

    const email = updates.email === undefined
      ? users[userIndex].email
      : normalizeEmail(updates.email);
    if (
      users.some(
        (user, index) => index !== userIndex && normalizeEmail(user.email) === email,
      )
    ) {
      throw new DuplicateEmailError();
    }

    const updatedUser: User = {
      ...users[userIndex],
      ...updates,
      email,
    };

    users[userIndex] = updatedUser;
    this.writeUsersToDisk(users);
    return updatedUser;
  }

  public async delete(id: string): Promise<boolean> {
    const users = this.readUsers();
    const filteredUsers = users.filter((user) => user.id !== id);

    if (users.length === filteredUsers.length) {
      return false;
    }

    this.writeUsersToDisk(filteredUsers);
    return true;
  }
}
